"""Paused project with configured checks for permission settings journeys."""

import json
import sys
from pathlib import Path

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import git
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPreparation, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.workspaces import Workspaces

store = Workspace(Path(sys.argv[1]))
project = sys.argv[2]

repo = store.directory / "fixture-repositories" / project
adopt(store, ProjectSetup(id=project, path=str(repo)))
git(repo, "init", "-b", "main")
(repo / "README.md").write_text("Saved checks fixture")
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
        project_id=project,
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
    workspace = Workspaces.read(db, project).id
print(json.dumps({"workspace": workspace, "project": project, "task": task.key}))
