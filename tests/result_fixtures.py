"""Reusable isolated test support; importing this module never collects a test suite."""

import os
from pathlib import Path

from project_fixtures import adopt, task_request

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskEdit, Workspace
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
    recipe=None,
    setup_commands=None,
    preview=None,
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
            setup_commands=setup_commands or [],
        ),
    )
    git(repo, "switch", "integration")
    if preview:
        from flowfield.inspection import Inspections

        Inspections(workspace).configure("harbor", preview)
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
    workspace.edit_task(
        "harbor",
        task.id,
        TaskEdit(
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
    report = WorkerResult(
        recipe=recipe,
        summary="Outcome",
        checks="Worker-reported checks",
        outcome=outcome,
        remaining_work=remaining_work,
    )
    if recipe is not None:
        service.execution.report_result("harbor", run.id, report)
    run = service.execution.finish(
        "harbor",
        run.id,
        "in_review",
        result=service.execution.get("harbor", run.id).result if recipe is not None else report,
        commit=commit,
    )
    settings = service.execution.settings("harbor")
    service.execution.queue("harbor", QueueEdit(expected_revision=settings.revision, enabled=False))
    return service, repo, run


def current(service):
    return service.results.page("harbor", "work").items[0]


def ready(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    approve(service, current(service))
    return service, repo, run


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
