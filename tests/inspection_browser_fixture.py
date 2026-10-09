"""Real Git browser fixture with synthetic reports and persistently paused model scheduling."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPreparation, Workspace
from flowfield.execution_models import SettingsEdit, WorkerResult
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig
from flowfield.integration_models import IntegrationConfig
from flowfield.supervisor import Supervisor

workspace = Workspace(Path(sys.argv[1]))
service = Supervisor(workspace)
project = "inspection-browser"
repo = workspace.directory / project
if sys.argv[2] == "create":
    adopt(workspace, ProjectSetup(path=str(repo), task_prefix="IPV"))
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("Inspection toy")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Baseline",
    )
    service.integrations.configure(
        project,
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="delivery",
            create_from="main",
            checks=["python app.py"],
        ),
    )
    git(repo, "switch", "delivery")
    service.execution.configure(
        project,
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fake", effort="low")),
    )
    Inspections(workspace).configure(
        project, InspectionConfig(expected_revision=1, run_command="python app.py")
    )
    workspace.create_task(
        project,
        task_request(
            id="try",
            title="Try the candidate",
            body="Make a useful greeting",
            status="up_next",
            preparation=TaskPreparation(completion="code"),
        ),
    )
settings = service.execution.settings(project).model_copy(update={"enabled": True})
head = service.integrations.head(project)
with patch.object(service.execution, "_settings", return_value=settings):
    run = service.execution.claim(project, head, service._available(project, repo, head))
assert run
service.execution.started(project, run.id)
env = LocalHost(os.environ).prepare(workspace.directory, repo, run.id, run.base_commit)
message = "First greeting" if sys.argv[2] == "create" else "Useful successor"
(env.checkout / "app.py").write_text(f"print({message!r})\n")
commit, _ = env.snapshot(run.base_commit)
service.execution.finish(
    project,
    run.id,
    "in_review",
    commit=commit,
    result=WorkerResult(summary=message, checks="Synthetic report; service runs Python."),
)
print(run.task_key)
