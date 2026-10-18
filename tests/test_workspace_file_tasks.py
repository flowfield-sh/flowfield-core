"""A workspace can deliver files without registering or initializing a repository."""

import asyncio
import json
from pathlib import Path

import pytest
from project_fixtures import task_request
from workspace_fixtures import TEST_AGENTS

from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentChoice
from flowfield.application import TaskPreparation, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, WorkerResult
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.results import Results
from flowfield.reviewer_settings import ReviewerEdit, Reviewers
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_managed_files_and_dependency_inputs_use_one_queue(tmp_path, monkeypatch):
    from fake_worker import FakeWorker

    from flowfield.supervisor import Supervisor

    errors = []

    class FileWorker(FakeWorker):
        async def prompt(self, text, on_permission, *, attachments=None):
            try:
                return await super().prompt(text, on_permission, attachments=attachments)
            except Exception as error:
                errors.append(str(error))
                raise

        async def run(self, model, effort, prompt, tools):
            brief = json.loads(prompt)
            assert not (self.cwd / ".git").exists()
            inputs = brief["input_files"]
            content = "First version"
            if inputs:
                assert len(inputs) == 1
                content = Path(inputs[0]["path"]).read_text() + " consumed"
            output = Path(brief["output_directory"]) / "report.txt"
            output.write_text(content)
            await self.on_tool(
                "submit_result",
                {
                    "outcome": "complete",
                    "summary": "Files ready",
                    "checks": "Read back bytes",
                    "files": [{"path": "report.txt"}],
                },
            )
            return {"status": "completed"}

    monkeypatch.setattr("flowfield.adapters.agent_selection.CodexAgent", FileWorker)
    monkeypatch.setattr("flowfield.supervisor.process_stamp", lambda pid: "fixture-process")
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    owner = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Files"))
    Reviewers(store).configure(owner.id, ReviewerEdit(expected_revision=1, enabled=False))
    scope = owner.id
    first = store.create_task(
        scope,
        task_request(
            title="Source",
            body="Write a file",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    second = store.create_task(
        scope,
        task_request(
            title="Consume",
            body="Use the source file",
            status="up_next",
            dependencies=[first.id],
            preparation=TaskPreparation(completion="report"),
        ),
    )
    settings = spaces.configure_workers(
        owner.id,
        SettingsEdit(
            expected_revision=1,
            selection=AgentChoice(model="test-model", effort="low"),
        ),
    )
    spaces.queue(owner.id, QueueEdit(expected_revision=settings.revision, enabled=True))
    service = Supervisor(store)
    from unittest.mock import AsyncMock

    service.validate_agent_choice = AsyncMock()

    async def exercise():
        await service.start()
        try:
            for _ in range(300):
                if store.task(scope, second.id).status == "done":
                    break
                await asyncio.sleep(0.05)
            runs = Execution(store).page(scope).items
            assert store.task(scope, second.id).status == "done", errors or [
                (r.status, r.problem) for r in runs
            ]
            assert len(runs) == 2
            assert all(run.result_commit is None and run.status == "accepted" for run in runs)
        finally:
            await service.close()

    asyncio.run(exercise())
    files = Outputs(store).page(owner.id).items
    assert [Outputs(store).content(file).read_text() for file in files] == [
        "First version consumed",
        "First version",
    ]
    assert files[0].result_id and files[1].result_id
    assert store.projects() == []


def test_inputs_reject_unknown_and_cross_workspace_files(tmp_path):
    store = Workspace(tmp_path / "state")
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Files"))
    Reviewers(store).configure(owner.id, ReviewerEdit(expected_revision=1, enabled=False))
    with pytest.raises(ApplicationError, match="submitted output or sent attachment"):
        store.create_task(
            owner.id,
            task_request(
                title="Invalid input",
                body="Read a missing file",
                input_files=["unknown"],
            ),
        )
    assert BrowserReads(store).workspace_board(owner.id).tasks == []


def test_workspace_file_result_without_git_survives_attempt_cleanup(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    owner = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Research"))
    scope = owner.id
    task = store.create_task(
        scope,
        task_request(
            title="Make a report",
            body="Produce a report and dataset.",
            status="up_next",
            preparation=TaskPreparation(completion="report"),
        ),
    )
    assert store.projects() == []
    assert spaces.projects(owner.id) == []
    assert BrowserReads(store).workspace_board(owner.id).tasks[0].id == task.id
    settings = spaces.configure_workers(
        owner.id,
        SettingsEdit(
            expected_revision=1,
            selection=AgentChoice(model="test-model"),
        ),
    )
    spaces.queue(owner.id, QueueEdit(expected_revision=settings.revision, enabled=True))
    execution = Execution(store)
    run = execution.claim(scope, "", {}, task_id=task.id)
    assert run and run.base_commit == ""
    environment = LocalHost({}).prepare_files(store.directory, owner.id, run.id)
    assert environment.common_git is None
    assert not (environment.checkout / ".git").exists()
    execution.save_local(run.id, {"runtime": str(environment.runtime)})
    execution.started(scope, run.id)
    (environment.runtime / "output" / "report.txt").write_text("Findings")
    report = WorkerResult(
        summary="Report ready",
        checks="Read back the report",
        files=[OutputSelection(path="report.txt")],
    )
    execution.report_result(scope, run.id, report)
    execution.finish(scope, run.id, "in_review", result=report)
    Reviewers(store).configure(scope, ReviewerEdit(expected_revision=1, enabled=False))
    Results(store).process(scope)
    result = Results(store).page(scope, task.id).items[0]
    assert result.status == "delivered"
    assert result.source_commit is None
    assert store.task(scope, task.id).status == "done"
    assert len(result.files) == 1
    import shutil

    shutil.rmtree(environment.root)
    restarted = Workspace(store.directory)
    output = Outputs(restarted).page(owner.id).items[0]
    assert output.project_id is None
    assert Outputs(restarted).content(output).read_text() == "Findings"
    assert not list(Path(store.directory).rglob(".git"))


def test_workspace_task_cannot_claim_repository_delivery(tmp_path):
    store = Workspace(tmp_path / "state")
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Research"))
    with pytest.raises(ApplicationError, match="Workspace tasks deliver files"):
        store.create_task(
            owner.id,
            task_request(
                title="Code",
                body="Change a repository",
                preparation=TaskPreparation(completion="code"),
            ),
        )


def test_revisions_keep_exact_inputs_and_old_files(tmp_path):
    import shutil

    from file_delivery_fixtures import deliver, setup
    from file_delivery_fixtures import task as make_task

    from flowfield.application import TaskPriority, TaskPublish, TaskReopen
    from flowfield.output_models import FileInput
    from flowfield.task_files import materialize

    store, owner, scope = setup(tmp_path / "state")
    task = make_task(store, scope)
    first = deliver(store, scope, task.id, {"report.txt": "Original"})
    dependent = make_task(store, scope, title="Use the original", dependencies=[task.key])
    execution = Execution(store)
    from unittest.mock import patch

    with patch.object(
        execution,
        "_settings",
        return_value=execution.settings(scope).model_copy(update={"enabled": True}),
    ):
        run = execution.claim(scope, "", {}, task_id=dependent.id)
    frozen = json.loads(execution.assignment(scope, run.id)["files"])
    assert frozen[0]["id"] == first.files[0].id
    execution.finish(scope, run.id, "stopped")
    current = store.task(scope, task.id)
    current = store.reopen_task(
        scope, task.id, TaskReopen(expected_revision=current.revision, note="Revise the findings")
    )
    current = store.prioritize_task(
        scope, task.id, TaskPriority(expected_revision=current.revision, status="up_next")
    )
    store.publish_task(
        scope,
        task.id,
        TaskPublish(
            expected_revision=current.revision,
            completion="report",
        ),
    )
    second = deliver(store, scope, task.id, {"report.txt": "Revised"})
    assert second.version == 2 and second.files[0].id != first.files[0].id
    shutil.rmtree(store.directory / "workspaces" / owner.id / "attempts")
    inputs = materialize(
        store, owner.id, [FileInput.model_validate(item) for item in frozen], tmp_path / "inputs"
    )
    assert Path(inputs[0]["path"]).read_text() == "Original"
    assert [
        Outputs(store).content(file).read_text() for file in Outputs(store).page(owner.id).items
    ] == ["Revised", "Original"]
    assert store.task(scope, dependent.id).reconciliation_reason


def test_workspace_coordinator_tools_create_and_read_file_tasks(tmp_path):
    from flowfield.agent_tools import workspace_scope as grant_scope
    from flowfield.supervisor import Supervisor

    store = Workspace(tmp_path / "state")
    owner = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Files"))
    Reviewers(store).configure(owner.id, ReviewerEdit(expected_revision=1, enabled=False))
    other = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other"))

    async def exercise():
        grant = await grant_scope(Supervisor(store), owner.id)
        created = await grant.call(
            "create_task",
            {"task": task_request(title="Research", body="Write a report").model_dump(mode="json")},
        )
        assert not created.isError, created
        assert created.structuredContent["key"] == "T-1"
        read = await grant.call("get_task", {"task_id": "T-1"})
        assert not read.isError, read
        board = await grant.call("get_board", {})
        assert not board.isError, board
        denied = await grant.call("get_board", {"workspace_id": other.id})
        assert denied.isError
        grant.revoke()

    asyncio.run(exercise())
    assert store.projects() == []
    separate = store.create_task(
        other.id, task_request(title="Separate report", body="Other workspace")
    )
    assert separate.key == "T-1"
    assert store.task(owner.id, "T-1").title == "Research"
    assert store.task(other.id, "T-1").id == separate.id


def test_sent_attachment_and_output_inputs_are_workspace_scoped(tmp_path):
    import base64

    from file_delivery_fixtures import deliver, setup
    from file_delivery_fixtures import task as make_task

    from flowfield.attachments import Attachments, AttachmentUpload
    from flowfield.task_files import freeze, materialize

    store, owner, scope = setup(tmp_path / "state")
    source = make_task(store, scope)
    file = deliver(store, scope, source.id, {"report.txt": "Private findings"}).files[0]
    other = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Other"))
    with pytest.raises(ApplicationError, match="submitted output or sent attachment"):
        make_task(store, other.id, input_files=[file.id])
    attachments = Attachments(store)
    upload = attachments.upload(
        owner.id,
        AttachmentUpload(
            name="input.txt", mime="text/plain", data=base64.b64encode(b"Original input").decode()
        ),
    )
    with store.connection(write=True) as db:
        with pytest.raises(ApplicationError):
            freeze(db, owner.id, [upload.id])
        attachments.references(db, owner.id, None, upload.href, bind=True)
        frozen = freeze(db, owner.id, [upload.id])
        with pytest.raises(ApplicationError):
            freeze(db, other.id, [upload.id])
    copies = materialize(store, owner.id, frozen, tmp_path / "inputs")
    assert Path(copies[0]["path"]).read_text() == "Original input"


def test_workspace_result_attention_uses_the_existing_notification_owner(tmp_path):
    from file_delivery_fixtures import deliver, setup
    from file_delivery_fixtures import task as make_task

    from flowfield.attention import actionable_attention, attention_page

    store, owner, scope = setup(tmp_path / "state")
    task = make_task(store, scope)
    result = deliver(
        store,
        scope,
        task.id,
        report=WorkerResult(
            outcome="partial",
            summary="Draft ready",
            remaining_work="Add the final section",
            checks="Draft inspected",
        ),
    )
    assert attention_page(store, scope, "action").items[0].id == result.id
    with store.connection() as db:
        assert [row["id"] for row in actionable_attention(db, scope)] == [result.id]
        event = db.execute(
            "SELECT data FROM announcement_events WHERE workspace_id=? AND state='pending'",
            (scope,),
        ).fetchone()
        assert event and result.id in event[0]


def test_missing_retained_file_blocks_delivery_and_recovery_needs_no_approval(
    tmp_path, monkeypatch
):
    from file_delivery_fixtures import deliver, setup
    from file_delivery_fixtures import task as make_task

    from flowfield.execution_models import RunAction

    store, owner, scope = setup(tmp_path / "state")
    task = make_task(store, scope)
    with monkeypatch.context() as paused:
        paused.setattr(Results, "process", lambda *args: None)
        result = deliver(store, scope, task.id, {"report.txt": "Exact bytes"})
    outputs = Outputs(store)
    path = outputs.content(result.files[0])
    path.write_text("Changed bytes")
    Results(store).process(scope)
    blocked = Results(store).get(scope, result.id)
    assert blocked.status == "blocked" and blocked.problem_code == "output_unavailable"
    assert store.task(scope, task.id).status != "done"
    assert blocked.next_action.label == "Retry file delivery"
    path.write_text("Exact bytes")
    retried = Results(store).reprepare(
        scope, result.id, RunAction(expected_revision=blocked.revision)
    )
    Results(store).process(scope)
    assert Results(store).get(scope, retried.id).status == "delivered"
    assert store.task(scope, task.id).status == "done"
    assert outputs.get(owner.id, result.files[0].id).id == result.files[0].id
