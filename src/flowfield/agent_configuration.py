"""Validated settings operations shared by browser and Coordinator transports."""

import os
from typing import TYPE_CHECKING

from flowfield.agent_models import AgentSettingsEdit, AgentSettingsView
from flowfield.agent_settings import AgentSettings
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind, HarnessStatus
from flowfield.harness_settings import HarnessSettings
from flowfield.reviewer_settings import ReviewerEdit, Reviewers, ReviewerSettingsView

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor


async def configure_worker(
    service: "Supervisor", workspace: str, task: str, request: AgentSettingsEdit
) -> AgentSettingsView:
    if request.selection:
        current = service.workspace.task(workspace, task)
        await service.validate_agent_choice(request.selection, current.project_id)
    return AgentSettings(service.workspace).edit(workspace, "worker", request, task)


async def configure_reviewer(
    service: "Supervisor", workspace: str, request: ReviewerEdit, entity: str = ""
) -> ReviewerSettingsView:
    owner = Reviewers(service.workspace)
    current = owner.get(workspace, entity=entity)
    service.workspace._current(current.revision, request.expected_revision)
    if request.selection and request.enabled is not False:
        from flowfield.adapters.coordinator_directory import prepare

        await service.validate_agent_choice(
            request.selection, None, project_path=str(prepare(service.workspace, workspace))
        )
    return owner.configure(workspace, request, entity=entity)


def harness_status(service: "Supervisor", harness: HarnessKind) -> HarnessStatus:
    registration = HarnessSettings(service.workspace).get(harness)
    return with_catalog(service, service.harness_checks.view(registration, os.environ))


def with_catalog(service: "Supervisor", value: HarnessStatus) -> HarnessStatus:
    value = value.model_copy(deep=True)
    value.catalog_ownership = service.catalogs.ownership(value.registration.harness)
    if value.catalog_ownership:
        value.problems.append("catalog_" + value.catalog_ownership.status)
        if value.catalog_ownership.status == "uncertain":
            value.selectable = False
    return value


async def check_harness(service: "Supervisor", harness: HarnessKind) -> HarnessStatus:
    registration = HarnessSettings(service.workspace).get(harness)
    checked = await service.harness_checks.run(registration, os.environ)
    if HarnessSettings(service.workspace).get(harness) != registration:
        raise ApplicationError(
            "harness_changed",
            "Host settings changed during the check. Reload and check again.",
            409,
        )
    return with_catalog(service, checked)
