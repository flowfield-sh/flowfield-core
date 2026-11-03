"""Coordinator settings, host readiness and exact recovery through application owners."""

from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from flowfield.agent_configuration import (
    check_harness,
    configure_reviewer,
    configure_worker,
    harness_status,
)
from flowfield.agent_models import AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.announcements import Announcements, AnnouncementSettings
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.errors import ApplicationError
from flowfield.execution_models import RunAction
from flowfield.harness_models import CatalogConfirmation, HarnessEdit, HarnessKind
from flowfield.harness_settings import HarnessSettings
from flowfield.permission_models import PermissionAnswer
from flowfield.reads import excerpt, receipt
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.source_navigation import navigate
from flowfield.supervisor import Supervisor
from flowfield.worker_checks import CheckPermissionEdit, CheckPermissions
from flowfield.workspaces import Workspaces

# Deliberate service-wide authority. Workspace binding must never be inferred or
# bypassed for other mutations just because a new tool omits its scope argument.
SERVICE_MUTATIONS = frozenset(
    {
        "configure_harness",
        "check_harness_setup",
        "confirm_harness_discovery_stopped",
        "configure_notifications",
        "configure_updates",
        "check_for_updates",
    }
)


def add_settings_tools(mcp: FastMCP, supervisor: Callable[[], Supervisor]) -> None:
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=read)
    def get_coordinator_settings(workspace_id: str) -> dict[str, Any]:
        """Read the workspace Coordinator choice and its current revision."""
        return CoordinatorSettings(supervisor().workspace).get(workspace_id).model_dump()

    @mcp.tool(annotations=write)
    async def configure_coordinator(
        workspace_id: str, request: AgentSettingsEdit
    ) -> dict[str, Any]:
        """Save the requested Coordinator choice for this workspace. Discover models first.
        A change requiring a fresh session is rejected while the Coordinator is active;
        ask the human to stop it and change that setting in the GUI or external MCP.
        """
        return (
            await supervisor().configure_workspace_coordinator(workspace_id, request)
        ).model_dump()

    @mcp.tool(annotations=read)
    def get_task_worker_settings(workspace_id: str, task_id: str) -> dict[str, Any]:
        """Read a task's Worker override, inherited choice and revision."""
        return (
            AgentSettings(supervisor().workspace).get(workspace_id, "worker", task_id).model_dump()
        )

    @mcp.tool(annotations=write)
    async def configure_task_worker(
        workspace_id: str, task_id: str, request: AgentSettingsEdit
    ) -> dict[str, Any]:
        """Set a task Worker choice, or clear selection to inherit workspace defaults.
        Existing attempts keep their frozen settings. Discover supported choices first.
        """
        return (await configure_worker(supervisor(), workspace_id, task_id, request)).model_dump()

    @mcp.tool(annotations=read)
    def get_reviewer_settings(workspace_id: str, task_id: str | None = None) -> dict[str, Any]:
        """Read workspace Reviewer defaults or one task override and its effective settings."""
        return (
            Reviewers(supervisor().workspace).get(workspace_id, entity=task_id or "").model_dump()
        )

    @mcp.tool(annotations=write)
    async def configure_reviewer_settings(
        workspace_id: str, request: ReviewerEdit, task_id: str | None = None
    ) -> dict[str, Any]:
        """Save requested Reviewer defaults or a task override. Discover models first.
        Disabling independent review never approves code delivery.
        """
        return (
            await configure_reviewer(supervisor(), workspace_id, request, task_id or "")
        ).model_dump()

    @mcp.tool(annotations=read)
    def get_worker_occupancy(workspace_id: str) -> dict[str, Any]:
        """Inspect occupied and uncertain worker slots without changing queue state."""
        return Workspaces(supervisor().workspace).occupancy(workspace_id).model_dump()

    @mcp.tool(annotations=read)
    def get_harness_settings(harness: HarnessKind) -> dict[str, Any]:
        """Inspect service-wide native paths, readiness and discovery ownership; runs nothing."""
        return harness_status(supervisor(), harness).model_dump()

    @mcp.tool(annotations=write)
    def configure_harness(harness: HarnessKind, request: HarnessEdit) -> dict[str, Any]:
        """Save requested service-wide native path overrides, affecting all workspaces.
        Null paths restore native detection; this does not sign out or disable a harness.
        Native configuration files and credentials are not changed.
        """
        return HarnessSettings(supervisor().workspace).edit(harness, request).model_dump()

    @mcp.tool(annotations=write)
    async def check_harness_setup(harness: HarnessKind) -> dict[str, Any]:
        """Run a native installation/sign-in check on the service host, without a model prompt."""
        return (await check_harness(supervisor(), harness)).model_dump()

    @mcp.tool(annotations=write)
    def confirm_harness_discovery_stopped(
        harness: HarnessKind, request: CatalogConfirmation
    ) -> dict[str, Any]:
        """Relay the human's confirmation that exact uncertain discovery was stopped on
        the host. Never infer cleanup from an interrupted conversation or elapsed time.
        """
        supervisor().catalogs.confirm_stopped(harness, request.id)
        return harness_status(supervisor(), harness).model_dump()

    @mcp.tool(annotations=read)
    def get_check_permission(project_id: str) -> dict[str, Any]:
        """Read whether the human authorized saved project checks for Workers."""
        return CheckPermissions(supervisor().workspace).get(project_id).model_dump()

    @mcp.tool(annotations=write)
    def configure_check_permission(project_id: str, request: CheckPermissionEdit) -> dict[str, Any]:
        """Relay explicit human authorization or revocation for the exact saved check
        commands and settings revision. Never infer permission from a task assignment.
        Changing commands invalidates the grant. This does not execute them.
        """
        return CheckPermissions(supervisor().workspace).configure(project_id, request).model_dump()

    @mcp.tool(annotations=read)
    def get_permission(
        workspace_id: str,
        permission_id: str,
        option_id: str | None = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=6000)] = 6000,
    ) -> dict[str, Any]:
        """Inspect an exact native permission from list_requests/get_request.
        The text page contains details, or the full offered label when option_id is given.
        Read any truncated option label completely before presenting its scope to the human.
        This does not answer the request or infer consent.
        """
        record = supervisor().permissions.get(workspace_id, permission_id)
        text = record.details
        if option_id is not None:
            option = next((item for item in record.options if item.id == option_id), None)
            if option is None:
                raise ApplicationError("invalid_option", "Choose an offered permission option.")
            text = option.label
        page = navigate(text, offset=offset, limit=limit)
        if page["next_request"]:
            page["next_request"] = {
                "offset": page["next_offset"],
                "limit": limit,
                "option_id": option_id,
            }
        return {
            **record.model_dump(exclude={"details", "options", "binding", "turn_id"}),
            "options": [excerpt(item.model_dump(), 200) for item in record.options],
            "source": {"field": "option" if option_id else "details", "option_id": option_id},
            **page,
        }

    @mcp.tool(annotations=write)
    async def answer_permission(
        workspace_id: str, permission_id: str, request: PermissionAnswer
    ) -> dict[str, Any]:
        """Relay the human's explicit choice for this exact pending permission and revision.
        Present the offered option and its scope first. Never approve your own request,
        invent an option or infer permission from the user's broader task instructions.
        """
        return supervisor().permissions.answer(workspace_id, permission_id, request).model_dump()

    @mcp.tool(annotations=read)
    def get_review(workspace_id: str, review_id: str) -> dict[str, Any]:
        """Inspect an exact review, findings, source revisions and recovery state.
        Read get_task/get_result and exact document revisions for full source content."""
        return (
            supervisor()
            .review_execution.reviews.get(workspace_id, review_id)
            .model_dump(exclude={"binding": True, "guidance": {"task_context", "result_context"}})
        )

    @mcp.tool(annotations=write)
    def retry_review(workspace_id: str, review_id: str, request: RunAction) -> dict[str, Any]:
        """Retry a failed/stopped independent review at its exact revision when requested.
        Preserves prior attempts and never approves delivery.
        """
        return receipt(
            supervisor()
            .review_execution.reviews.retry(workspace_id, review_id, request.expected_revision)
            .model_dump()
        )

    @mcp.tool(annotations=write)
    def confirm_review_stopped(
        workspace_id: str, review_id: str, request: RunAction
    ) -> dict[str, Any]:
        """Relay human confirmation that this review's remaining native work was stopped
        on the host. Never infer cleanup. Read the exact review revision first.
        """
        return receipt(
            supervisor()
            .review_execution.confirm_stopped(workspace_id, review_id, request.expected_revision)
            .model_dump()
        )

    @mcp.tool(annotations=read)
    def get_notification_settings() -> dict[str, Any]:
        """Read service-wide notification preferences. Browser permission is separate."""
        return Announcements(supervisor().workspace).settings().model_dump()

    @mcp.tool(annotations=write)
    def configure_notifications(request: AnnouncementSettings) -> dict[str, Any]:
        """Change requested service-wide notification preferences; omitted fields stay unchanged.
        System notifications still require browser/OS permission from the human.
        """
        return Announcements(supervisor().workspace).configure(request, partial=True).model_dump()

    @mcp.tool(annotations=read)
    def get_update_status() -> dict[str, Any]:
        """Read installed/latest version and service-wide automatic update-check preferences."""
        return supervisor().updates.status().model_dump()

    @mcp.tool(annotations=write)
    def configure_updates(automatic: bool) -> dict[str, Any]:
        """Set service-wide automatic update checks when requested. Installs nothing."""
        return supervisor().updates.configure(automatic).model_dump()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    )
    async def check_for_updates() -> dict[str, Any]:
        """Start a shared, rate-limited release check against PyPI. Installs nothing.
        Read get_update_status for completion.
        """
        return supervisor().updates.trigger().model_dump()
