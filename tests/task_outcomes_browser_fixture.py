"""Seed real Git and retained binary outputs with scripted contributions and review."""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

from workspace_fixtures import TEST_AGENTS

from flowfield.adapters.git_workspace import baseline, git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import (
    ProjectSetup,
    TaskCreate,
    TaskPreparation,
    TaskPriority,
    TaskPublish,
    Workspace,
)
from flowfield.execution import Execution
from flowfield.execution_models import SettingsEdit, WorkerResult
from flowfield.integration import Integrations
from flowfield.integration_models import IntegrationConfig
from flowfield.output_models import OutputSelection
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.subtasks import SubtaskCreate, Subtasks
from flowfield.task_assembly import TaskAssembly
from flowfield.task_reviews import ReviewVerdict, TaskReviews
from flowfield.workspaces import WorkspaceCreate, Workspaces


def seed(state: Path, assets: Path, agents=None):
    store = Workspace(state)
    space = Workspaces(store).create(
        WorkspaceCreate(**(agents or TEST_AGENTS), name="Task outcomes")
    )
    owner = space.id
    execution = Execution(store)
    execution.configure(
        owner,
        SettingsEdit(
            expected_revision=1,
            max_parallel=2,
            selection=AgentChoice.model_validate(agents["worker"])
            if agents
            else AgentChoice(model="scripted-walkthrough"),
        ),
    )
    Reviewers(store).configure(
        owner,
        ReviewerEdit(
            expected_revision=1,
            enabled=True,
            selection=AgentChoice.model_validate(agents["reviewer"])
            if agents
            else AgentChoice(model="scripted-review"),
        ),
    )

    def claim(task, base="", **values):
        settings = execution.settings(owner).model_copy(update={"enabled": True})
        with patch.object(execution, "_settings", return_value=settings):
            run = execution.claim(owner, base, {base: set()}, task_id=task.id, **values)
        assert run
        execution.started(owner, run.id)
        return run

    def files(run, env, names):
        execution.save_local(
            run.id,
            {
                "runtime": str(env.runtime),
                "root": str(env.root),
                "checkout": str(env.checkout),
                "runtime_kind": "local",
            },
        )
        for name in names:
            shutil.copyfile(assets / name, env.runtime / "output" / name)
        return [
            OutputSelection(path=name, description="Retained walkthrough output") for name in names
        ]

    def signoff(task):
        results = Results(store)
        results.process(owner)
        result = results.page(owner, task.id).items[0]
        reviews = TaskReviews(store)
        review = reviews.prepare(owner, result.id)
        settings = execution.settings(owner).model_copy(update={"enabled": True})
        with patch("flowfield.task_reviews.read_workspace", return_value=settings):
            review = reviews.claim(owner, review.id, review.revision)
        review = reviews.begin_attempt(owner, review.id)
        review = reviews.started(owner, review.id, review.revision)
        decision = ReviewVerdict(
            action="approve",
            summary="The outcome meets the task criteria.",
            evidence=(
                "Scripted review of exact PDF/PNG bytes and the combined candidate; "
                "no live model was used."
            ),
        )
        reviews.save_decision(owner, review.id, review.attempt_id, decision.model_dump())
        reviews.finish_attempt(owner, review.id, review.attempt_id, cleanup_confirmed=True)
        return results.get(owner, result.id)

    report = store.create_task(
        owner,
        TaskCreate(
            title="Create a launch brief PDF",
            body=(
                "Summarize the release and include a useful visual. "
                "Keep the PDF and screenshot available after work finishes."
            ),
            acceptance_criteria=(
                "The PDF opens, its text is readable, and the preview matches the release."
            ),
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    run = claim(report)
    env = LocalHost(os.environ).prepare_files(store.directory, owner, run.id)
    outputs = files(run, env, ["launch-brief.pdf", "launch-preview.png"])
    result = WorkerResult(
        summary="The launch brief and its visual are ready.",
        checks="Rendered the PDF and captured the preview from the same page.",
        files=outputs,
    )
    execution.report_result(owner, run.id, result)
    execution.finish(owner, run.id, "in_review", result=result)
    signoff(report)
    shutil.rmtree(env.root)

    repo = state / "fixture-repositories" / owner
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("# Harbor\nA disposable task walkthrough.\n")
    project = Workspaces(store).adopt(owner, ProjectSetup(path=str(repo), name="Harbor"))
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Baseline",
    )
    Integrations(store).configure(
        project.id,
        IntegrationConfig(
            expected_revision=1,
            target_branch="main",
            runtime="local",
            checks=["test -f api.js && test -f index.html"],
        ),
    )
    feature = store.create_task(
        owner,
        TaskCreate(
            title="Build the release overview",
            project_id=project.id,
            body=(
                "Create a release overview with its API, "
                "and deliver a PDF and screenshot alongside the code."
            ),
            acceptance_criteria=(
                "The API and page agree, both outputs are readable, "
                "and the whole result is reviewed."
            ),
        ),
    )
    children = Subtasks(store)
    api = children.create(
        owner,
        feature.id,
        SubtaskCreate(title="Release API", body="Provide release data", completion="code"),
    )
    ui = children.create(
        owner,
        feature.id,
        SubtaskCreate(title="Overview page", body="Present release information", completion="code"),
    )
    captures = children.create(
        owner,
        feature.id,
        SubtaskCreate(
            title="Capture deliverables",
            body="Capture the assembled overview and PDF",
            completion="report",
            dependencies=[api.key, ui.key],
        ),
    )
    feature = store.task(owner, feature.id)
    feature = store.publish_task(
        owner, feature.id, TaskPublish(expected_revision=feature.revision, completion="code")
    )
    feature = store.prioritize_task(
        owner, feature.id, TaskPriority(expected_revision=feature.revision, status="up_next")
    )
    assemblies = TaskAssembly(store)
    original = baseline(repo)
    assembly = assemblies.ensure(feature, original, [])
    for child, name, text in [
        (api, "api.js", 'export const release = {name: "Harbor", ready: true};\n'),
        (ui, "index.html", (assets / "overview.html").read_text()),
        (captures, None, None),
    ]:
        head, inputs = assemblies.baseline(assembly, child)
        run = claim(
            feature, head, subtask_id=child.id, assembly_id=assembly.id, contributions=inputs
        )
        env = LocalHost(os.environ).prepare(store.directory, repo, run.id, head)
        selected = []
        if name:
            (env.checkout / name).write_text(text)
        else:
            selected = files(run, env, ["launch-brief.pdf", "launch-preview.png"])
        commit, _ = env.snapshot(head)
        result = WorkerResult(
            summary=child.title + " is ready.",
            checks="Verified the contribution against its assignment.",
            files=selected,
        )
        execution.report_result(owner, run.id, result)
        execution.finish(owner, run.id, "in_review", result=result, commit=commit)
    assert assemblies.assemble(owner, feature.id)
    reviewed = signoff(feature)
    waiting = store.create_task(
        owner,
        TaskCreate(
            title="Capture the delivered feature",
            body="Capture the final integrated feature after delivery.",
            dependencies=[feature.key],
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    return {
        "workspace": owner,
        "report": report.key,
        "feature": feature.key,
        "dependent": waiting.key,
        "result": reviewed.id,
        "candidate": reviewed.candidate_commit,
        "repository": str(repo),
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]), Path(sys.argv[2]))))
