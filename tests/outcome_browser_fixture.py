"""Outcome/recovery browser states; synthetic reports, real Git, model queue stays paused."""

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
from flowfield.integration_models import IntegrationConfig
from flowfield.supervisor import Supervisor

workspace = Workspace(Path(sys.argv[1]))
service = Supervisor(workspace)
for kind in ("report", "partial", "checks", "setup"):
    project = f"outcome-{kind}"
    repo = workspace.directory / project
    adopt(
        workspace,
        ProjectSetup(
            path=str(repo),
            task_prefix={"report": "ORP", "partial": "OPT", "checks": "OCK", "setup": "OST"}[kind],
        ),
    )
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("Outcome fixture")
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
            checks=["exit 7"] if kind == "checks" else ["true"],
            setup_commands=["exit 3"] if kind == "setup" else [],
        ),
    )
    git(repo, "switch", "delivery")
    service.execution.configure(
        project,
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fake", effort="low")),
    )
    workspace.create_task(
        project,
        task_request(
            id="work",
            title=f"{kind.title()} outcome",
            body="Deliver both requested behaviors",
            status="up_next",
            preparation=TaskPreparation(completion="report" if kind == "report" else "code"),
        ),
    )
    settings = service.execution.settings(project).model_copy(update={"enabled": True})
    head = service.integrations.head(project)
    with patch.object(service.execution, "_settings", return_value=settings):
        run = service.execution.claim(project, head, service._available(project, repo, head))
    assert run
    service.execution.started(project, run.id)
    env = LocalHost(os.environ).prepare(workspace.directory, repo, run.id, run.base_commit)
    if kind != "report":
        (env.checkout / "result.txt").write_text("Useful change")
    commit, _ = env.snapshot(run.base_commit)
    service.execution.finish(
        project,
        run.id,
        "in_review",
        commit=commit,
        result=WorkerResult(
            summary="Useful findings" if kind == "report" else "First behavior implemented",
            checks="Synthetic worker claims; service runs configured checks",
            outcome="partial" if kind == "partial" else "complete",
            remaining_work="Finish the second behavior" if kind == "partial" else "",
        ),
    )
