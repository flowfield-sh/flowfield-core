"""Fresh task ownership and local contributions; no live model calls."""

import json

import pytest
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_models import AgentChoice
from flowfield.application import (
    TaskCreate,
    TaskEdit,
    TaskPreparation,
    TaskPriority,
    TaskPublish,
    TaskReconcile,
    Workspace,
)
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.result_models import ResultReview
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.subtasks import SubtaskCreate, SubtaskEdit, Subtasks
from flowfield.task_assembly import TaskAssembly
from flowfield.workspaces import WorkspaceCreate, Workspaces


def workspace(tmp_path, *, review=False):
    store = Workspace(tmp_path / "state")
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Outcomes"))
    if not review:
        Reviewers(store).configure(owner.id, ReviewerEdit(expected_revision=1, enabled=False))
    execution = Execution(store)
    configured = execution.configure(
        owner.id,
        SettingsEdit(
            expected_revision=1,
            max_parallel=2,
            selection=AgentChoice(harness="codex", model="deterministic-test"),
        ),
    )
    execution.queue(owner.id, QueueEdit(expected_revision=configured.revision, enabled=True))
    return store, owner.id, execution


def contribute(store, execution, owner, run, *, output=None, partial=False):
    execution.started(owner, run.id)
    files = []
    if output:
        runtime = store.directory.parent / run.id
        (runtime / "output").mkdir(parents=True)
        (runtime / "output" / output).write_bytes(b"%PDF-1.4\nretained test evidence\n")
        execution.save_local(run.id, {"runtime": str(runtime)})
        files = [OutputSelection(path=output)]
    result = WorkerResult(
        summary="Contribution ready",
        checks="Checked exact output",
        files=files,
        outcome="partial" if partial else "complete",
        remaining_work="Finish output" if partial else "",
    )
    execution.report_result(owner, run.id, result)
    return execution.finish(owner, run.id, "in_review", result=result)


def decomposed(store, owner):
    task = store.create_task(
        owner, TaskCreate(title="Publish a report", body="Research and design a report")
    )
    children = Subtasks(store)
    research = children.create(
        owner, task.id, SubtaskCreate(title="Research", body="Find evidence", completion="report")
    )
    design = children.create(
        owner, task.id, SubtaskCreate(title="Design", body="Design a cover", completion="report")
    )
    report = children.create(
        owner,
        task.id,
        SubtaskCreate(
            title="Report",
            body="Use both contributions",
            completion="report",
            dependencies=[research.key, design.key],
        ),
    )
    task = store.task(owner, task.id)
    task = store.publish_task(
        owner, task.id, TaskPublish(expected_revision=task.revision, completion="report")
    )
    task = store.prioritize_task(
        owner, task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    return task, research, design, report


def test_task_and_subtask_graphs_are_distinct_and_transactional(tmp_path):
    store, owner, _ = workspace(tmp_path)
    first = store.create_task(owner, TaskCreate(title="First", body="A report"))
    second = store.create_task(
        owner, TaskCreate(title="Second", body="A screenshot", dependencies=[first.key])
    )
    assert first.key == "T-1" and second.key == "T-2" and first.project_id is None
    with pytest.raises(ApplicationError, match="cycle"):
        store.edit_task(
            owner, first.id, TaskEdit(expected_revision=first.revision, dependencies=[second.key])
        )
    children = Subtasks(store)
    a = children.create(
        owner, first.id, SubtaskCreate(title="A", body="First contribution", completion="report")
    )
    b = children.create(
        owner,
        first.id,
        SubtaskCreate(
            title="B", body="Second contribution", completion="report", dependencies=[a.key]
        ),
    )
    with pytest.raises(ApplicationError, match="cycle"):
        children.edit(
            owner, first.id, a.id, SubtaskEdit(expected_revision=a.revision, dependencies=[b.key])
        )
    assert children.get(owner, first.id, a.id).dependencies == []
    from flowfield.thread_view import ThreadView

    messages = ThreadView(store).page_view(owner, first.id).items
    definitions = sorted(
        (message for message in messages if message.kind in ("definition", "subtasks")),
        key=lambda message: message.revision or 0,
    )
    assert definitions[0].title == "Task defined"
    assert [message.title for message in definitions[1:]] == [
        "Subtasks updated",
        "Subtasks updated",
    ]
    assert "S-1: A" in definitions[1].body and "First contribution" in definitions[1].body
    assert "S-2: B" not in definitions[1].body
    assert "S-2: B" in definitions[2].body and "Prerequisites: S-1" in definitions[2].body
    with pytest.raises(ApplicationError):
        children.create(
            owner,
            second.id,
            SubtaskCreate(
                title="Invalid", body="Cross-parent", completion="report", dependencies=[a.id]
            ),
        )
    other = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    own = store.create_task(other.id, TaskCreate(title="Independent"))
    assert own.key == "T-1"
    with pytest.raises(ApplicationError):
        store.create_task(
            other.id, TaskCreate(title="Invalid task dependency", dependencies=[second.id])
        )


def test_direct_files_survive_attempt_and_require_configured_review(tmp_path):
    store, owner, execution = workspace(tmp_path, review=True)
    task = store.create_task(
        owner,
        TaskCreate(
            title="PDF",
            body="Produce a PDF",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    run = execution.claim(owner, "", {}, task_id=task.id)
    assert run
    contribute(store, execution, owner, run, output="report.pdf")
    Results(store).process(owner)
    result = Results(store).page(owner, task.id).items[0]
    assert result.status == "ready" and store.task(owner, task.id).status == "in_progress"
    assert not Results(store).finish_unreviewed_report(owner, result.id)
    original = result.files[0]
    (tmp_path / run.id / "output" / "report.pdf").unlink()
    assert Outputs(store).content(original).read_bytes().startswith(b"%PDF")
    Reviewers(store).configure(owner, ReviewerEdit(expected_revision=1, enabled=False))
    assert Results(store).finish_unreviewed_report(owner, result.id)
    assert store.task(owner, task.id).status == "done"


def test_parallel_contributions_exact_inputs_and_single_delivery(tmp_path):
    store, owner, execution = workspace(tmp_path)
    task, a, b, c = decomposed(store, owner)
    assemblies = TaskAssembly(store)
    assembly = assemblies.ensure(task, "", [])
    first = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )
    second = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=b.id, assembly_id=assembly.id
    )
    assert first and second
    assignment = execution.assignment(owner, first.id)
    agreement = json.loads(assignment["agreement"])
    assert agreement["repository"] is False
    assert agreement["agreement_revision"] == task.agreement_revision
    assert agreement["completion"] == a.completion
    assert assignment["description"] == a.body
    assert assignment["task_intent"] == task.body
    assert json.loads(assignment["contribution"])["id"] == a.id
    assert (
        execution.claim(owner, "", {}, task_id=task.id, subtask_id=c.id, assembly_id=assembly.id)
        is None
    )
    contribute(store, execution, owner, first, output="research.pdf")
    contribute(store, execution, owner, second)
    baseline, inputs = assemblies.baseline(assembly, c)
    last = execution.claim(
        owner,
        baseline,
        {},
        task_id=task.id,
        subtask_id=c.id,
        assembly_id=assembly.id,
        contributions=inputs,
    )
    assert last
    assert json.loads(execution.assignment(owner, last.id)["files"])[0]["name"] == "research.pdf"
    contribute(store, execution, owner, last, output="report.pdf")
    assert assemblies.assemble(owner, task.id)
    assert not TaskAssembly(Workspace(store.directory)).assemble(owner, task.id)
    result = Results(store).page(owner, task.id).items[0]
    assert {file.name for file in result.files} == {"research.pdf", "report.pdf"}
    assert result.run_id not in {first.id, second.id, last.id}
    from flowfield.thread_view import ThreadView

    assembled = ThreadView(store).item_view(owner, task.id, "attempt:" + result.run_id)
    assert assembled.author == assembled.actor.identity == "flowfield"
    assert assembled.state.tone == "complete" and not assembled.has_activity and not assembled.body
    Results(store).process(owner)
    assert store.task(owner, task.id).status == "done"
    assert len(Results(store).page(owner, task.id).items) == 1


def test_assembled_review_receives_frozen_sources_and_output_manifest(tmp_path):
    from flowfield.documents import DocumentCreate, DocumentEdit, Documents
    from flowfield.task_reviews import TaskReviews

    store, owner, execution = workspace(tmp_path, review=True)
    docs = Documents(store)
    contract = docs.create(
        owner, DocumentCreate(title="Product contract", body="Required rule\n" * 800)
    )
    unrelated = docs.create(owner, DocumentCreate(title="Other work", body="Not selected"))
    task = store.create_task(
        owner,
        TaskCreate(
            title="Apply the contract",
            body="Verify the selected product rules",
            documents=[{"id": contract.id, "revision": 1}],
        ),
    )
    child = Subtasks(store).create(
        owner,
        task.id,
        SubtaskCreate(title="Research", body="Read the contract", completion="report"),
    )
    task = store.task(owner, task.id)
    task = store.publish_task(
        owner, task.id, TaskPublish(expected_revision=task.revision, completion="report")
    )
    task = store.prioritize_task(
        owner, task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    assemblies = TaskAssembly(store)
    assembly = assemblies.ensure(task, "", [])
    run = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=child.id, assembly_id=assembly.id
    )
    assert run
    contribute(store, execution, owner, run, output="contract.pdf")
    assert assemblies.assemble(owner, task.id)
    Results(store).process(owner)
    result = Results(store).page(owner, task.id).items[0]
    reviews = TaskReviews(store)
    review = reviews.prepare(owner, result.id)
    docs.edit(owner, contract.id, DocumentEdit(expected_revision=1, body="New, unselected rules"))
    assert reviews.read_context(review, f"document:{contract.id}") == contract.body
    assert json.loads(reviews.read_context(review, "files")) == []
    assert result.report.files == []
    output = json.loads(reviews.read_context(review, "result"))["files"][0]
    assert output["id"] == result.files[0].id
    assert output["name"] == "contract.pdf"
    assert output["sha256"] == result.files[0].sha256
    assert output["size"] == result.files[0].size
    manifest = json.loads(reviews.read_context(review, "documentation"))
    selected = next(item for item in manifest["items"] if item["id"] == contract.id)
    assert selected["revision"] == 1 and selected["section"] == f"document:{contract.id}"
    with pytest.raises(ApplicationError, match="section"):
        reviews.read_context(review, f"document:{unrelated.id}")
    # Restarted reads still use captured content, not the latest document revision.
    restarted = TaskReviews(Workspace(store.directory))
    assert restarted.read_context(review, f"document:{contract.id}") == contract.body
    assert json.loads(restarted.read_context(review, "result"))["files"][0] == output


def test_editorial_guidance_preserves_assembled_report_and_contributions(tmp_path):
    from flowfield.documents import DocumentEdit, Documents
    from flowfield.guidance_reconciliation import prepare
    from flowfield.task_reviews import TaskReviews

    store, owner, execution = workspace(tmp_path, review=True)
    task = store.create_task(owner, TaskCreate(title="Summarize the pilot", body="A short report"))
    child = Subtasks(store).create(
        owner,
        task.id,
        SubtaskCreate(title="Research", body="Inspect the pilot", completion="report"),
    )
    task = store.task(owner, task.id)
    task = store.publish_task(
        owner, task.id, TaskPublish(expected_revision=task.revision, completion="report")
    )
    task = store.prioritize_task(
        owner, task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    assemblies = TaskAssembly(store)
    assembly = assemblies.ensure(task, "", [])
    run = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=child.id, assembly_id=assembly.id
    )
    assert run
    contribute(store, execution, owner, run, output="pilot.pdf")
    assert assemblies.assemble(owner, task.id)
    results = Results(store)
    results.process(owner)
    original = results.page(owner, task.id).items[0]
    frozen = execution.assignment(owner, original.run_id)
    docs = Documents(store)
    guidance = docs.get(owner, "guidance")
    docs.edit(
        owner,
        "guidance",
        DocumentEdit(expected_revision=guidance.revision, body="Use concise labels."),
    )
    reviews = TaskReviews(store)
    review = prepare(reviews, owner, task.id)
    assert review
    assert assemblies.ensure(store.task(owner, task.id), "", []).id == assembly.id
    review = reviews.claim(owner, review.id, review.revision)
    review = reviews.begin_attempt(owner, review.id)
    review = reviews.started(owner, review.id, review.revision)
    reviews.save_decision(
        owner,
        review.id,
        review.attempt_id,
        {
            "action": "adopt",
            "summary": "Clarified the existing outcome",
            "evidence": "The retained report already meets this wording.",
            "acceptance_criteria": "A concise pilot report.",
            "rework": False,
        },
    )
    reviews.finish_attempt(owner, review.id, review.attempt_id, cleanup_confirmed=True)
    refreshed = results.page(owner, task.id).items[0]
    assert refreshed.version == 2 and refreshed.run_id == original.run_id
    assert refreshed.output_ids == original.output_ids
    assert assemblies.ensure(store.task(owner, task.id), "", []).id == assembly.id
    assert not assemblies.assemble(owner, task.id)
    assert Subtasks(store).get(owner, task.id, child.id).contribution_run_id == run.id
    assert execution.assignment(owner, original.run_id) == frozen
    results.process(owner)
    review = reviews.prepare(owner, refreshed.id)
    assert reviews.assignment(review)["criteria"] == "A concise pilot report."
    review = reviews.claim(owner, review.id, review.revision)
    review = reviews.begin_attempt(owner, review.id)
    review = reviews.started(owner, review.id, review.revision)
    reviews.save_decision(
        owner,
        review.id,
        review.attempt_id,
        {
            "action": "approve",
            "summary": "Report meets the clarified outcome",
            "evidence": "Read the retained report and current guidance.",
        },
    )
    reviews.finish_attempt(owner, review.id, review.attempt_id, cleanup_confirmed=True)
    results.process(owner)
    assert store.task(owner, task.id).status == "done"
    assert results.get(owner, refreshed.id).next_action.label == "Delivered"


def test_correction_preserves_independent_contribution(tmp_path):
    store, owner, execution = workspace(tmp_path, review=True)
    task, a, b, c = decomposed(store, owner)
    assemblies = TaskAssembly(store)
    assembly = assemblies.ensure(task, "", [])
    runs = []
    for child in (a, b, c):
        baseline, inputs = assemblies.baseline(assembly, child)
        run = execution.claim(
            owner,
            baseline,
            {},
            task_id=task.id,
            subtask_id=child.id,
            assembly_id=assembly.id,
            contributions=inputs,
        )
        assert run
        contribute(store, execution, owner, run)
        runs.append(run)
    assert assemblies.assemble(owner, task.id)
    Results(store).process(owner)
    result = Results(store).page(owner, task.id).items[0]
    Results(store).review(
        owner,
        result.id,
        ResultReview(
            expected_revision=result.revision,
            candidate_commit=None,
            action="request_changes",
            subtask_id=a.id,
            note="Correct the source citation",
        ),
    )
    children = Subtasks(store)
    assert children.get(owner, task.id, b.id).contribution_run_id == runs[1].id
    assert children.get(owner, task.id, a.id).contribution_run_id is None
    assert children.get(owner, task.id, c.id).contribution_run_id is None
    assert not assemblies.assemble(owner, task.id)
    retry = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )
    assert retry and retry.feedback == "Correct the source citation"
    assert Results(store).get(owner, result.id).status == "changes_requested"
    contribute(store, execution, owner, retry)
    _, inputs = assemblies.baseline(assembly, children.get(owner, task.id, c.id))
    rebuilt = execution.claim(
        owner,
        "",
        {},
        task_id=task.id,
        subtask_id=c.id,
        assembly_id=assembly.id,
        contributions=inputs,
    )
    assert rebuilt and rebuilt.predecessor_id == runs[2].id
    assert "Correct the source citation" in execution.assignment(owner, rebuilt.id)["feedback"]
    assert execution.get(owner, runs[1].id).feedback == ""


@pytest.mark.parametrize("dependent_contribution", [False, True])
@pytest.mark.parametrize(
    "correction_source",
    ["reply", "plan", "stopped", "failed", "partial", "replanned_reply", "replanned_correction"],
)
def test_code_assembly_review_exact_approval_and_delivered_dependency(
    tmp_path, dependent_contribution, correction_source
):
    import os

    from flowfield.adapters.git_workspace import baseline, git
    from flowfield.adapters.local_execution import LocalHost
    from flowfield.application import ProjectSetup
    from flowfield.integration import Integrations
    from flowfield.integration_models import IntegrationConfig
    from flowfield.task_reviews import ReviewVerdict, TaskReviews

    store, owner, execution = workspace(tmp_path, review=True)
    repo = tmp_path / "application"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("Application\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Base",
    )
    project = Workspaces(store).adopt(owner, ProjectSetup(path=str(repo)))
    git(repo, "add", ".flowfield/config.toml")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Adopt project",
    )
    integrations = Integrations(store)
    integrations.configure(
        project.id,
        IntegrationConfig(
            expected_revision=1,
            runtime="local",
            target_branch="main",
            checks=["test -f api.txt && test -f ui.txt"],
        ),
    )
    Reviewers(store).configure(
        owner,
        ReviewerEdit(
            expected_revision=1,
            enabled=True,
            selection=AgentChoice(harness="codex", model="deterministic-review"),
        ),
    )
    task = store.create_task(
        owner,
        TaskCreate(
            title="Implement feature",
            body="Build API and UI",
            acceptance_criteria="Both pieces work together",
            project_id=project.id,
        ),
    )
    children = Subtasks(store)
    a = children.create(
        owner, task.id, SubtaskCreate(title="API", body="Implement API", completion="code")
    )
    b = children.create(
        owner,
        task.id,
        SubtaskCreate(
            title="UI",
            body="Implement UI",
            completion="code",
            dependencies=[a.id] if dependent_contribution else [],
        ),
    )
    task = store.task(owner, task.id)
    task = store.publish_task(
        owner, task.id, TaskPublish(expected_revision=task.revision, completion="code")
    )
    task = store.prioritize_task(
        owner, task.id, TaskPriority(expected_revision=task.revision, status="up_next")
    )
    dependent = store.create_task(
        owner,
        TaskCreate(
            title="Capture screenshots",
            body="Capture delivered UI",
            dependencies=[task.key],
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    original = baseline(repo)
    assemblies = TaskAssembly(store)
    assembly = assemblies.ensure(task, original, [])
    for child, name in [(a, "api.txt"), (b, "ui.txt")]:
        child_base, inputs = assemblies.baseline(assembly, child)
        run = execution.claim(
            owner,
            child_base,
            {child_base: set()},
            task_id=task.id,
            subtask_id=child.id,
            assembly_id=assembly.id,
            contributions=inputs,
        )
        assert run
        execution.started(owner, run.id)
        environment = LocalHost(os.environ).prepare(store.directory, repo, run.id, child_base)
        (environment.checkout / name).write_text(child.title)
        if child.id == a.id:
            (environment.checkout / "api-contract.txt").write_text("Keep this contract")
        commit, _ = environment.snapshot(child_base)
        result = WorkerResult(summary=f"{child.title} complete", checks="Verified file")
        execution.report_result(owner, run.id, result)
        execution.finish(owner, run.id, "in_review", result=result, commit=commit)
    assert baseline(repo) == original
    assert execution.claim(owner, "", {}, task_id=dependent.id) is None
    assert assemblies.assemble(owner, task.id)
    assert baseline(repo) == original
    Results(store).process(owner)
    result = Results(store).page(owner, task.id).items[0]
    assert result.status == "ready", result
    assert result.candidate_commit
    assert git(repo, "show", result.candidate_commit + ":api.txt") == b"API"
    assert git(repo, "show", result.candidate_commit + ":ui.txt") == b"UI"
    # A correction replaces only the selected contribution and reassembles real Git code.
    previous_ui = children.get(owner, task.id, b.id).contribution_run_id
    from flowfield.conversation import Conversation
    from flowfield.replies import Replies
    from flowfield.reply_models import ReplyBinding, ReplyCreate

    if correction_source.startswith("replanned_"):
        from flowfield.execution_models import RunAction

        task = store.task(owner, task.id)
        store.edit_task(
            owner,
            task.id,
            TaskEdit(expected_revision=task.revision, body="Build API and UI for all adapters"),
        )
        for child in children.all(owner, task.id):
            fresh = children.get(owner, task.id, child.id)
            children.edit(
                owner,
                task.id,
                child.id,
                SubtaskEdit(expected_revision=fresh.revision, body="Support every adapter"),
            )
        task = store.task(owner, task.id)
        task = store.reconcile_task(
            owner,
            task.id,
            TaskReconcile(expected_revision=task.revision, note="All adapters"),
        )
        (repo / "upstream.txt").write_text("Current release")
        git(repo, "add", "upstream.txt")
        git(
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Current release",
        )
        advanced = baseline(repo)
        integrations.refresh_availability(project.id)
        stale = Results(store).get(owner, result.id)
        assert stale.status == "stale"
        assert all(child.contribution_run_id is None for child in children.all(owner, task.id))
        if correction_source == "replanned_correction":
            Results(store).correct(
                owner,
                result.id,
                RunAction(expected_revision=stale.revision, note="Finish all adapters"),
            )
        else:
            gate = Conversation(store).eligibility(owner, task.id)
            Replies(store).submit(
                owner,
                task.id,
                ReplyCreate(
                    binding=ReplyBinding.model_validate(
                        gate.model_dump(include=set(ReplyBinding.model_fields))
                    ),
                    action="changes",
                    body="Finish all adapters",
                ),
            )
        retained = Results(store).get(owner, result.id)
        assert retained.status == "changes_requested" and retained.approved_at is None
        assert retained.source_commit == result.source_commit
        assert git(repo, "show", retained.source_commit + ":api.txt") == b"API"
        task = store.task(owner, task.id)
        assert task.status == "up_next"
        fresh_assembly = assemblies.ensure(task, advanced, [])
        assert fresh_assembly.id != assembly.id and fresh_assembly.base_commit == advanced
        fresh = execution.claim(
            owner,
            advanced,
            {advanced: set()},
            task_id=task.id,
            subtask_id=a.id,
            assembly_id=fresh_assembly.id,
        )
        assert fresh and fresh.base_commit == advanced and fresh.correction is None
        assert baseline(repo) == advanced
        return

    if correction_source != "reply":
        previous = children.get(owner, task.id, a.id)
        children.edit(
            owner,
            task.id,
            a.id,
            SubtaskEdit(expected_revision=previous.revision, body="Version the API response"),
        )
        task = store.task(owner, task.id)
        task = store.reconcile_task(
            owner,
            task.id,
            TaskReconcile(expected_revision=task.revision, note="Refined API contribution"),
        )
        assert assemblies.ensure(task, original, []).id == assembly.id
    else:
        gate = Conversation(store).eligibility(owner, task.id)
        request = ReplyCreate(
            binding=ReplyBinding.model_validate(
                gate.model_dump(include=set(ReplyBinding.model_fields))
            ),
            action="changes",
            subtask_id=a.key,
            body="Version the API response",
        )
        with pytest.raises(ApplicationError, match="Subtask not found"):
            Replies(store).submit(owner, task.id, request.model_copy(update={"subtask_id": "S-99"}))
        assert children.get(owner, task.id, b.id).contribution_run_id == previous_ui
        reply = Replies(store).submit(owner, task.id, request)
        assert reply.subtask_id == a.key
        assert Replies(store).submit(owner, task.id, request) == reply
        with pytest.raises(ApplicationError, match="already used"):
            Replies(store).submit(owner, task.id, request.model_copy(update={"subtask_id": b.key}))
    corrected = children.get(owner, task.id, a.id)
    correction_base, inputs = assemblies.baseline(assembly, corrected)
    assert git(repo, "show", correction_base + ":api.txt") == b"API"
    assert git(repo, "show", correction_base + ":api-contract.txt") == b"Keep this contract"
    assert inputs == []  # The prior contribution is a predecessor, not its own dependency.
    retry = execution.claim(
        owner,
        correction_base,
        {correction_base: set()},
        task_id=task.id,
        subtask_id=a.id,
        assembly_id=assembly.id,
        contributions=inputs,
    )
    assert retry
    if correction_source == "reply":
        assert retry.feedback == "Version the API response"
    else:
        assert execution.assignment(owner, retry.id)["description"] == "Version the API response"
    execution.started(owner, retry.id)
    environment = LocalHost(os.environ).prepare(store.directory, repo, retry.id, correction_base)
    if correction_source in ("stopped", "failed", "partial"):
        import asyncio

        from flowfield.supervisor import Supervisor

        (environment.checkout / "recovery.txt").write_text("Retained unfinished work")
        if correction_source == "partial":
            partial_commit, _ = environment.snapshot(correction_base)
            partial = WorkerResult(
                summary="Source changes preserved",
                checks="Checked partial work",
                outcome="partial",
                remaining_work="Finish the source provenance",
            )
            execution.report_result(owner, retry.id, partial)
            saved = execution.finish(
                owner, retry.id, "in_review", result=partial, commit=partial_commit
            )
            assert saved.status == "failed" and saved.checkpoint_commit is None
        else:
            asyncio.run(Supervisor(store)._checkpoint(retry, environment))
            execution.finish(owner, retry.id, correction_source)
        previous = children.get(owner, task.id, a.id)
        children.edit(
            owner,
            task.id,
            a.id,
            SubtaskEdit(expected_revision=previous.revision, body="Resume the API correction"),
        )
        task = store.task(owner, task.id)
        task = store.reconcile_task(
            owner,
            task.id,
            TaskReconcile(expected_revision=task.revision, note="Clarified recovery instructions"),
        )
        assert assemblies.ensure(task, original, []).id == assembly.id
        correction_base, inputs = assemblies.baseline(assembly, children.get(owner, task.id, a.id))
        assert git(repo, "show", correction_base + ":recovery.txt") == b"Retained unfinished work"
        assert git(repo, "show", correction_base + ":api-contract.txt") == b"Keep this contract"
        previous_run = retry
        retry = execution.claim(
            owner,
            correction_base,
            {correction_base: set()},
            task_id=task.id,
            subtask_id=a.id,
            assembly_id=assembly.id,
            contributions=inputs,
        )
        assert retry and retry.predecessor_id == previous_run.id
        assert (retry.input_base_commit or retry.base_commit) == correction_base
        execution.started(owner, retry.id)
        environment = LocalHost(os.environ).prepare(
            store.directory, repo, retry.id, correction_base
        )
    (environment.checkout / "api.txt").write_text("API v2")
    corrected_commit, _ = environment.snapshot(correction_base)
    report = WorkerResult(summary="API versioned", checks="Checked the revised API")
    execution.report_result(owner, retry.id, report)
    execution.finish(owner, retry.id, "in_review", result=report, commit=corrected_commit)
    if dependent_contribution:
        assert children.get(owner, task.id, b.id).contribution_run_id is None
        refreshed = children.get(owner, task.id, b.id)
        refreshed_base, refreshed_inputs = assemblies.baseline(assembly, refreshed)
        assert git(repo, "show", refreshed_base + ":api.txt") == b"API v2"
        assert git(repo, "show", refreshed_base + ":ui.txt") == b"UI"
        assert [item.id for item in refreshed_inputs] == [retry.id]
        refreshed_run = execution.claim(
            owner,
            refreshed_base,
            {refreshed_base: set()},
            task_id=task.id,
            subtask_id=b.id,
            assembly_id=assembly.id,
            contributions=refreshed_inputs,
        )
        assert refreshed_run and refreshed_run.predecessor_id == previous_ui
        execution.started(owner, refreshed_run.id)
        execution.report_result(owner, refreshed_run.id, report)
        execution.finish(owner, refreshed_run.id, "in_review", result=report, commit=refreshed_base)
    else:
        assert children.get(owner, task.id, b.id).contribution_run_id == previous_ui
    assert assemblies.assemble(owner, task.id)
    Results(store).process(owner)
    result = Results(store).page(owner, task.id).items[0]
    assert result.candidate_commit
    assert git(repo, "show", result.candidate_commit + ":api.txt") == b"API v2"
    assert git(repo, "show", result.candidate_commit + ":api-contract.txt") == b"Keep this contract"
    assert git(repo, "show", result.candidate_commit + ":ui.txt") == b"UI"
    assert baseline(repo) == original
    approval = ResultReview(
        expected_revision=result.revision,
        candidate_commit=result.candidate_commit,
        action="approve",
    )
    with pytest.raises(ApplicationError, match="configured review"):
        Results(store).review(owner, result.id, approval)
    reviews = TaskReviews(store)
    review = reviews.prepare(owner, result.id)
    review = reviews.claim(owner, review.id, review.revision)
    review = reviews.started(owner, review.id, review.revision)
    review = reviews.conclude(
        owner,
        review.id,
        review.revision,
        ReviewVerdict(
            action="approve", summary="Feature passes", evidence="Inspected both exact files"
        ),
        cleanup_confirmed=True,
    )
    assert baseline(repo) == original
    Results(store).review(owner, result.id, approval)
    assert reviews.check(owner, review.id).status == "decided"
    assert execution.claim(owner, "", {}, task_id=dependent.id) is None
    # Reconstruct owners to exercise durable approval through service restart.
    restarted = Workspace(store.directory)
    Results(restarted).process(owner)
    delivered = Results(restarted).get(owner, result.id)
    assert delivered.status == "delivered", (delivered.problem_code, delivered.problem)
    assert TaskReviews(restarted).check(owner, review.id).status == "decided"
    assert Results(restarted).get(owner, result.id).independent_review.status == "decided"
    assert baseline(repo) == result.candidate_commit
    assert restarted.task(owner, task.id).status == "done"
    assert Execution(restarted).claim(owner, "", {}, task_id=dependent.id)
    Results(restarted).process(owner)
    assert baseline(repo) == result.candidate_commit


def test_task_stop_and_restart_keep_capacity_and_preserved_contributions(tmp_path):
    import asyncio

    from flowfield.execution_models import RunAction
    from flowfield.supervisor import Supervisor
    from flowfield.worker_settings import occupancy

    store, owner, execution = workspace(tmp_path)
    task, a, b, _ = decomposed(store, owner)
    assembly = TaskAssembly(store).ensure(task, "", [])
    first = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )
    second = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=b.id, assembly_id=assembly.id
    )
    assert first and second
    execution.restart()
    with store.connection() as db:
        assert occupancy(db, workspace=owner).uncertain == 2
    assert (
        execution.claim(owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id)
        is None
    )
    current = store.task(owner, task.id)
    stopped = asyncio.run(
        Supervisor(store).stop_task(owner, task.id, RunAction(expected_revision=current.revision))
    )
    assert len(stopped) == 2 and all(run.status == "stopped" for run in stopped)
    with store.connection() as db:
        assert occupancy(db, workspace=owner).uncertain == 0
    assert store.task(owner, task.id).status == "backlog"
    assert not execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )


def test_changed_parent_inputs_cannot_claim_or_assemble_old_contributions(tmp_path):
    store, owner, execution = workspace(tmp_path)
    task, a, _, _ = decomposed(store, owner)
    assembly = TaskAssembly(store).ensure(task, "", [])
    updated = store.edit_task(
        owner, task.id, TaskEdit(expected_revision=task.revision, body="Use different evidence")
    )
    updated = store.publish_task(
        owner, task.id, TaskPublish(expected_revision=updated.revision, completion="report")
    )
    with pytest.raises(ApplicationError, match="prerequisites changed"):
        execution.claim(owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id)
    assert not TaskAssembly(store).assemble(owner, task.id)
    fresh = TaskAssembly(store).ensure(updated, "", [])
    assert fresh.id != assembly.id


def test_parallel_subtask_questions_resume_without_blocking_siblings(tmp_path):
    from flowfield.conversation import Conversation
    from flowfield.questions import QuestionAnswer, QuestionCreate, Questions

    store, owner, execution = workspace(tmp_path)
    task, a, b, _ = decomposed(store, owner)
    assembly = TaskAssembly(store).ensure(task, "", [])
    first = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )
    second = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=b.id, assembly_id=assembly.id
    )
    questions = Questions(store)
    pending = []
    for run in (first, second):
        execution.started(owner, run.id)
        question = execution.ask_question(
            owner,
            run.id,
            QuestionCreate(
                task_id=task.id,
                question="Which scope?",
                context="Clarify this contribution",
                recommendation="Use the agreed scope",
                blocking_scope="Contribution",
            ),
        )
        execution.finish(owner, run.id, "waiting_for_input", input_checkpoint="files")
        assert Conversation(store).eligibility(owner, task.id, question.id).enabled
        pending.append(question)
    questions.answer(
        owner, pending[0].id, QuestionAnswer(expected_revision=1, answer="Use the agreed scope")
    )
    resumed = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=a.id, assembly_id=assembly.id
    )
    assert resumed and resumed.input_question_id == pending[0].id
    execution.started(owner, resumed.id)
    assert Conversation(store).eligibility(owner, task.id, pending[1].id).enabled
    questions.answer(
        owner, pending[1].id, QuestionAnswer(expected_revision=1, answer="Use the agreed scope")
    )
    other = execution.claim(
        owner, "", {}, task_id=task.id, subtask_id=b.id, assembly_id=assembly.id
    )
    assert other and other.input_question_id == pending[1].id
    assert execution.occupancy(owner).active == 2
