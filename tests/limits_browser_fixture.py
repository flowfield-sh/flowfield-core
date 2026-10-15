"""Model-free exhaustion through the real execution owner, with a paused queue."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import git
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPreparation, Workspace
from flowfield.execution import Execution
from flowfield.execution_limits import ExecutionLimits, PolicyEdit
from flowfield.execution_models import SettingsEdit
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.worker_settings import workspace_id

store = Workspace(Path(sys.argv[1]))
project = sys.argv[2]


def exhaust() -> None:
    execution = Execution(store)
    settings = execution.settings(project)
    with patch.object(
        execution, "_settings", return_value=settings.model_copy(update={"enabled": True})
    ):
        run = execution.claim(project, "a" * 40, {"a" * 40: set()})
    assert run
    execution.started(project, run.id)
    limits = ExecutionLimits(store)
    remaining = limits.get(project, run.task_id)
    assert remaining
    limits.clock(project, run.id, active=True, at=100)
    limits.clock(
        project,
        run.id,
        active=None,
        at=101 + remaining.active_seconds_allowed - remaining.active_seconds_used,
    )
    execution.finish(
        project, run.id, "stopped", problem="Active execution allowance reached; work preserved."
    )


if sys.argv[3:] == ["exhaust"]:
    exhaust()
    sys.exit(0)

repo = store.directory / "fixture-repositories" / project
adopt(store, ProjectSetup(id=project, path=str(repo)))
git(repo, "init", "-b", "main")
(repo / "README.md").write_text("Local limits fixture")
git(repo, "add", ".")
git(
    repo, "-c", "user.name=Fixture", "-c", "user.email=test@example.invalid", "commit", "-m", "base"
)
Integrations(store).configure(
    project,
    IntegrationConfig(expected_revision=1, target_branch="main", checks=["test -f README.md"]),
)
task = store.create_task(
    project,
    task_request(
        title="Finish the inventory export",
        body="Complete the saved export contract and verify the HTTP behavior.",
        status="up_next",
        preparation=TaskPreparation(completion="report"),
    ),
)
execution = Execution(store)
settings = execution.configure(
    project, SettingsEdit(expected_revision=1, selection=AgentChoice(model="fixture", effort="low"))
)
with store.connection() as db:
    workspace = workspace_id(db, project)
limits = ExecutionLimits(store)
limits.configure(workspace, PolicyEdit(expected_revision=1, active_seconds=60, corrections=2))
with patch.object(
    execution, "_settings", return_value=settings.model_copy(update={"enabled": True})
):
    run = execution.claim(project, "a" * 40, {"a" * 40: set()})
execution.started(project, run.id)
limits.clock(project, run.id, active=True, at=100)
limits.clock(project, run.id, active=None, at=161)
execution.finish(
    project, run.id, "stopped", problem="Active execution allowance reached; work preserved."
)
print(
    json.dumps(
        {
            "workspace": workspace,
            "project": project,
            "task": task.key,
            "limit": limits.get(project, task.id).model_dump(),
        }
    )
)
