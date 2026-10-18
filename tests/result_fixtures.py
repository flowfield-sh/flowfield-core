"""Reusable isolated test support; importing this module never collects a test suite."""

import os
from pathlib import Path

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.integration_models import IntegrationConfig
from flowfield.result_models import ResultReview
from flowfield.supervisor import Supervisor


def fixture(
    tmp_path: Path,
    *,
    completion="code",
    changed=True,
    checks=None,
    outcome="complete",
    remaining_work="",
):
    workspace = Workspace(tmp_path / "state")
    repo = tmp_path / "harbor"
    adopt(workspace, ProjectSetup(path=str(repo)))
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "base",
    )
    from flowfield.reviewer_settings import ReviewerEdit, Reviewers

    Reviewers(workspace).configure("harbor", ReviewerEdit(expected_revision=1, enabled=False))
    service = Supervisor(workspace)
    service.integrations.configure(
        "harbor",
        IntegrationConfig(
            runtime="local",
            expected_revision=1,
            target_branch="integration",
            create_from="main",
            checks=checks or ["test -f result.txt"],
        ),
    )
    git(repo, "switch", "integration")
    task = workspace.create_task(
        "harbor",
        task_request(
            project_id="harbor",
            id="work",
            title="Work",
            body="Deliver the result",
            status="up_next",
        ),
    )
    workspace.publish_task(
        "harbor",
        task.id,
        TaskPublish(
            completion=completion,
            expected_revision=task.revision,
        ),
    )
    settings = service.execution.configure(
        "harbor",
        SettingsEdit(expected_revision=1, selection=AgentChoice(model="fixture", effort="low")),
    )
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=True))
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    assert run
    service.execution.started("harbor", run.id)
    env = LocalHost(os.environ).prepare(workspace.directory, repo, run.id, base)
    if changed:
        (env.checkout / "result.txt").write_text("implemented\n")
    commit, _ = env.snapshot(base)
    run = service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=WorkerResult(
            summary="Outcome",
            checks="Worker-reported checks",
            outcome=outcome,
            remaining_work=remaining_work,
        ),
        commit=commit,
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    return service, repo, run


def current(service):
    return service.results.page("harbor", "work").items[0]


def approve(service, version):
    return service.results.review(
        "harbor",
        version.id,
        ResultReview(
            expected_revision=version.revision,
            candidate_commit=version.candidate_commit or version.source_commit,
            action="approve",
        ),
    )
