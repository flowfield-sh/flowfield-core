"""Coordinator tools for managed work; workers never receive this MCP server."""

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from flowfield.execution_history import ExecutionHistory
from flowfield.execution_models import QueueEdit, RunAction, SettingsEdit
from flowfield.harness_models import HarnessKind
from flowfield.integration_models import IntegrationConfig
from flowfield.reads import MAX_LIMIT, ContextReads, receipt
from flowfield.result_models import ResultReview
from flowfield.setup_validation import SetupCheckRequest
from flowfield.supervisor import Supervisor

PageLimit = Annotated[int, Field(ge=1, le=MAX_LIMIT)]


def add_execution_tools(
    mcp: FastMCP, supervisor: Callable[[], Supervisor], reads: Callable[[], ContextReads]
) -> None:
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=read)
    def get_executions(
        project_id: str,
        task_id: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: PageLimit = 20,
    ) -> dict[str, Any]:
        """Page typed worker, validation and delivery executions with result relationships.
        Human approval is a result decision, not an execution.
        """
        return (
            ExecutionHistory(supervisor().workspace)
            .page(project_id, task_id, max(0, offset), max(1, min(limit, 50)))
            .model_dump()
        )

    @mcp.tool(annotations=read)
    def get_results(
        project_id: str,
        task_id: str,
        before: int | None = None,
        limit: Annotated[int, Field(ge=1, le=10)] = 10,
    ) -> dict[str, Any]:
        """Read proposed result versions, validation and delivery state; newest is current.
        Follow integration_id for observed checks and combined changes.
        """
        from flowfield.reads import size

        page = supervisor().results.page(project_id, task_id, before, min(limit, 10)).model_dump()
        page["items"] = [reads().result(project_id, item["id"]) for item in page["items"]]
        while len(page["items"]) > 1 and size(page) > 23000:
            page["items"].pop()
            page["next_before"] = page["items"][-1]["version"]
        return page

    @mcp.tool(annotations=read)
    def get_result(project_id: str, result_id: str) -> dict[str, Any]:
        """Inspect one exact proposed-result version, its approval and service delivery."""
        return reads().result(project_id, result_id)

    @mcp.tool(annotations=write)
    def review_result(project_id: str, result_id: str, review: ResultReview) -> dict[str, Any]:
        """Record explicit human approval of the exact validated candidate and request delivery,
        or request changes. Never infer approval. The service finishes authorized delivery
        after this receipt, even when the conversation closes.
        Check its outcome before claiming Done.
        Requesting changes can immediately start eligible work. Revise/reconcile agreed intent
        first when feedback changes it; do not edit the assignment after scheduling a follow-up.
        """
        return receipt(supervisor().results.review(project_id, result_id, review).model_dump())

    @mcp.tool(annotations=write)
    def prepare_result(project_id: str, result_id: str, request: RunAction) -> dict[str, Any]:
        """Create a fresh unapproved version against the current target/settings.
        Preserves prior candidates and decisions. Use for moved targets or fixed setup/checks.
        """
        return receipt(supervisor().results.reprepare(project_id, result_id, request).model_dump())

    @mcp.tool(annotations=write)
    async def correct_result(project_id: str, result_id: str, request: RunAction) -> dict[str, Any]:
        """Request one correction using configured worker model/effort and queue capacity.
        Carries both code inputs and conflict/check evidence. Two correction attempts per task;
        no automatic retry and no inherited approval. Reconcile changed scope first.
        """
        return receipt(
            (
                await asyncio.to_thread(
                    supervisor().results.correct, project_id, result_id, request
                )
            ).model_dump()
        )

    @mcp.tool(annotations=write)
    def cancel_result(project_id: str, result_id: str, request: RunAction) -> dict[str, Any]:
        """Cancel before branch application begins. Preserve code; running checks may finish."""
        return receipt(supervisor().results.cancel(project_id, result_id, request).model_dump())

    @mcp.tool(annotations=write)
    async def revalidate_result(
        project_id: str, result_id: str, request: RunAction
    ) -> dict[str, Any]:
        """Check current target availability for historically delivered code, without changing Git.
        Read availability_id for observed checks. Done stays historical.
        """
        return receipt(
            (
                await asyncio.to_thread(
                    supervisor().results.revalidate, project_id, result_id, request
                )
            ).model_dump()
        )

    @mcp.tool(annotations=write)
    async def list_worker_models(
        project_id: str | None = None,
        harness: HarnessKind = "codex",
    ) -> list[dict[str, Any]]:
        """Discover exact native choices for a harness and optional project; no model prompt.

        Starts a disposable native session when no current catalog is cached; native
        startup hooks can run. Pass project_id for project-policy choices. No selection
        is changed. Interrupted
        native discovery can require host inspection and human confirmation before retry.
        """
        return [
            item.model_dump()
            for item in await supervisor().model_options(
                project_id=project_id,
                harness=harness,
            )
        ]

    @mcp.tool(annotations=read)
    def get_workers(project_id: str) -> dict[str, Any]:
        """Read queue enabled/paused state, explicit model/effort, cap and setup problems."""
        return supervisor().execution.settings(project_id).model_dump()

    @mcp.tool(annotations=write)
    async def configure_workers(project_id: str, settings: SettingsEdit) -> dict[str, Any]:
        """Save the human's explicit worker model/effort and cap. Does not enable the queue."""
        return (await supervisor().configure(project_id, settings)).model_dump()

    @mcp.tool(annotations=write)
    def set_queue(project_id: str, change: QueueEdit) -> dict[str, Any]:
        """Enable eligible Up next starts, or pause new reservations. Pause does not stop runs."""
        return supervisor().execution.queue(project_id, change).model_dump()

    @mcp.tool(annotations=read)
    def get_runs(
        project_id: str,
        task_id: str | None = None,
        before: int | None = None,
        limit: PageLimit = 10,
    ) -> dict[str, Any]:
        """Page attempt summaries; request get_run for a result's checks/limitations/location."""
        page = supervisor().execution.page(project_id, task_id=task_id, before=before, limit=limit)
        return {
            "items": [
                {
                    "id": run.id,
                    "url": reads().url(project_id, "tasks", run.task_key, "runs", run.id),
                    "task_key": run.task_key,
                    "revision": run.revision,
                    "status": run.status,
                    "model": run.model,
                    "effort": run.effort,
                    "problem": run.problem,
                    "result_commit": run.result_commit,
                    "code_available": run.code_available,
                }
                for run in page.items
            ],
            "next_before": page.next_before,
        }

    @mcp.tool(annotations=read)
    def get_run(project_id: str, run_id: str) -> dict[str, Any]:
        """Inspect one attempt, attributed usage and exact Git result/checks with local
        inspection commands.
        """
        run = supervisor().execution.get(project_id, run_id)
        return {
            **run.model_dump(),
            "url": reads().url(project_id, "tasks", run.task_key, "runs", run.id),
            "location": supervisor().location(project_id, run_id).model_dump(),
        }

    @mcp.tool(annotations=write)
    async def stop_run(project_id: str, run_id: str, request: RunAction) -> dict[str, Any]:
        """Stop/reconcile this attempt's owned process and tracked tools; preserve its code."""
        return (await supervisor().stop(project_id, run_id, request)).model_dump()

    @mcp.tool(annotations=write)
    def retry_run(project_id: str, run_id: str, request: RunAction) -> dict[str, Any]:
        """Explicitly select a fresh attempt after a confirmed failure/stop/input
        resolution. Queues the task for a fresh attempt; returns the updated prior attempt.
        Starts depend on queue/capacity; inspect task/get_runs for the new attempt.
        Preserve prior files; no automatic retry.
        """
        return supervisor().execution.retry(project_id, run_id, request).model_dump()

    @mcp.tool(annotations=read)
    def get_integration_settings(project_id: str) -> dict[str, Any]:
        """Read destination, setup/check commands and Local environment.
        No Git mutations or commands are run.
        """
        settings = supervisor().integrations.settings(project_id)
        return {
            **settings.model_dump(),
            "environment_info": {
                "runtime": "local",
                "description": "Service host tools, credentials and native harness settings; "
                "per-attempt "
                "checkout and temporary/output paths.",
            },
        }

    @mcp.tool(annotations=write)
    def retry_result_delivery(
        project_id: str, result_id: str, request: RunAction
    ) -> dict[str, Any]:
        """Retry approved integration after resolving a checkout blocker.
        Preserves exact approval; changed code, settings, scope or destination require
        fresh preparation and approval.
        """
        return receipt(
            supervisor().results.retry_delivery(project_id, result_id, request).model_dump()
        )

    @mcp.tool(annotations=write)
    def configure_integration(project_id: str, settings: IntegrationConfig) -> dict[str, Any]:
        """Save the human's destination, setup, environment and trusted check commands.
        For a NEW agreed branch set settings.target_branch to its name and
        settings.create_from to an explicit baseline if needed; otherwise a missing
        branch is created from the project's current HEAD in this same call.
        The service creates it even when the coordinator shell is read-only; do not
        require manual Git creation. Omit create_from for an existing branch. Never
        resets a branch or starts workers. settings.expected_revision comes from
        get_integration_settings, not the project revision.
        """
        return supervisor().integrations.configure(project_id, settings).model_dump()

    @mcp.tool(annotations=read)
    def get_setup_validation(project_id: str) -> dict[str, Any] | None:
        """Read observed setup evidence and whether its settings or destination changed."""
        value = supervisor().setup_validation.get(project_id)
        return value.model_dump() if value else None

    @mcp.tool(annotations=write)
    async def validate_project_setup(project_id: str, request: SetupCheckRequest) -> dict[str, Any]:
        """Run saved setup/check commands through the actual managed worker boundary, without
        a model call or enabling the queue. Creates a separate retained checkout. Requires
        explicit adoption/setup intent; reports missing tools and stale configuration."""
        return (await supervisor().setup_validation.check(project_id, request)).model_dump()

    @mcp.tool(annotations=read)
    def get_integrations(
        project_id: str, run_id: str | None = None, before: int | None = None, limit: PageLimit = 10
    ) -> dict[str, Any]:
        """Page integration summaries; get_integration returns exact check output and location."""
        page = supervisor().integrations.page(project_id, run_id, before, limit)
        return {
            "items": [item.model_dump(exclude={"checks"}) for item in page.items],
            "next_before": page.next_before,
        }

    @mcp.tool(annotations=read)
    def get_integration(project_id: str, integration_id: str) -> dict[str, Any]:
        """Inspect one candidate, target-before/after, validation output and preserved checkout."""
        return supervisor().integrations.get(project_id, integration_id).model_dump()
