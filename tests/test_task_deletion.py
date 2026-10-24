"""Permanent deletion crosses database, owned filesystem and exact-result boundaries."""

import json
from pathlib import Path

import pytest
from project_fixtures import task_request
from result_fixtures import approve, current, fixture

from flowfield.adapters.git_workspace import baseline, git
from flowfield.application import Edit, TaskCreate, TaskEdit
from flowfield.browser import BrowserReads
from flowfield.browser_tabs import resource_presence
from flowfield.errors import ApplicationError
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionPrepare
from flowfield.questions import QuestionCreate, Questions, QuestionWithdraw
from flowfield.task_deletion import TaskDeletionService


def delete(service, identity="work"):
    task = service.workspace.task("harbor", identity)
    return TaskDeletionService(service.workspace).delete(
        "harbor", identity, Edit(expected_revision=task.revision)
    )


def test_delete_delivered_task_removes_records_and_dirty_inspections_not_delivered_code(tmp_path):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    inspections = Inspections(service.workspace)
    copies = [
        inspections.prepare(
            "harbor",
            InspectionPrepare(
                result_id=result.id, expected_revision=result.revision, new_copy=True
            ),
        )
        for _ in range(2)
    ]
    for copy in copies:
        (Path(copy.workspace) / "untracked.txt").write_text("preview edits")
    questions = Questions(service.workspace)
    question = questions.ask(
        "harbor",
        QuestionCreate(
            task_id="work",
            question="Keep the next iteration small?",
            context="Plan follow-up",
            recommendation="Yes",
        ),
    )
    questions.withdraw(
        "harbor",
        question.id,
        QuestionWithdraw(expected_revision=question.revision, reason="Resolved during inspection."),
    )
    approve(service, result)
    service.results.process("harbor")
    delivered = current(service)
    assert delivered.status == "delivered"
    (repo / "personal.txt").write_text("preserve adopted repository")
    deleted = delete(service)
    assert not deleted.cleanup_pending
    assert baseline(repo) == delivered.candidate_commit
    assert (repo / "result.txt").read_text() == "implemented\n"
    assert (repo / "personal.txt").read_text() == "preserve adopted repository"
    assert all(not Path(copy.workspace).parent.exists() for copy in copies)
    assert not (service.workspace.directory / "local-attempts" / run.id).exists()
    assert b"inspection-work" not in git(repo, "worktree", "list", "--porcelain")
    with service.workspace.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in (
            "tasks",
            "task_revisions",
            "runs",
            "result_versions",
            "questions",
            "question_revisions",
            "activity",
            "search_docs",
            "inspections",
            "integrations",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == (
                1 if table == "search_docs" else 0
            )
        saved = json.loads(db.execute("SELECT data FROM task_deletions").fetchone()[0])
        assert saved["paths"] == []
        assert "title" not in saved and "body" not in saved
    assert BrowserReads(service.workspace).workspace_board("harbor").tasks == []
    locations = [
        f"/workspaces/harbor/tasks/{run.task_key}",
        f"/workspaces/harbor/results/{result.id}",
    ]
    assert not any(resource_presence(service.workspace, "harbor", locations).values())
    # Lost DELETE receipts can be retried safely and old URLs never bind a new task.
    assert (
        TaskDeletionService(service.workspace).delete(
            "harbor", run.task_key, Edit(expected_revision=1)
        )
        == deleted
    )
    replacement = service.workspace.create_task("harbor", TaskCreate(title="Next task"))
    assert replacement.key == "T-2"
    with pytest.raises(ApplicationError, match="ID was deleted"):
        service.workspace.create_task("harbor", TaskCreate(title="Reused", id="work"))


def test_delete_checks_revision_ownership_and_dependencies_before_touching_files(tmp_path):
    service, _, run = fixture(tmp_path)
    cleanup = TaskDeletionService(service.workspace)
    with pytest.raises(ApplicationError, match="stale"):
        cleanup.delete("harbor", "work", Edit(expected_revision=1))
    # A preparing result is managed work even though the native worker has stopped.
    with pytest.raises(ApplicationError, match="active checks"):
        delete(service)
    service.results.process("harbor")
    other = service.workspace.create_task(
        "harbor", task_request(project_id="harbor", title="Uses this result", dependencies=["work"])
    )
    with pytest.raises(ApplicationError, match="depends on"):
        delete(service)
    assert (service.workspace.directory / "local-attempts" / run.id).exists()
    delete(service, other.id)
    with service.workspace.connection(write=True) as db:
        run = service.execution._run(db, "harbor", run.id)
        run.status = "uncertain"
        service.execution._save(db, run)
    with pytest.raises(ApplicationError, match="stop the worker"):
        delete(service)


def test_cleanup_failure_is_durable_and_retry_removes_only_owned_paths(tmp_path, monkeypatch):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    cleanup = TaskDeletionService(service.workspace)
    original = cleanup._remove_path
    monkeypatch.setattr(cleanup, "_remove_path", lambda _: (_ for _ in ()).throw(OSError("busy")))
    task = service.workspace.task("harbor", "work")
    deleted = cleanup.delete("harbor", "work", Edit(expected_revision=task.revision))
    assert deleted.cleanup_pending and deleted.problem
    with pytest.raises(ApplicationError, match="not found"):
        service.workspace.task("harbor", "work")
    assert (service.workspace.directory / "local-attempts" / run.id).exists()
    monkeypatch.setattr(cleanup, "_remove_path", original)
    assert not cleanup.delete(
        "harbor", "work", Edit(expected_revision=task.revision)
    ).cleanup_pending
    assert (repo / "README.md").exists()


def test_delete_rejects_frozen_prerequisite_consumers(tmp_path):
    service, _, run = fixture(tmp_path)
    service.results.process("harbor")
    other = service.workspace.create_task("harbor", TaskCreate(title="Other task"))
    # A real frozen prerequisite is a JSON section within the assignment, not a live edge.
    with service.workspace.connection(write=True) as db:
        source = db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone()
        data = json.loads(source["data"])
        data.update(id="consumer", task_id=other.id, task_key=other.key)
        db.execute(
            "INSERT INTO runs(id,workspace_id,task_id,status,data,assignment) VALUES (?,?,?,?,?,?)",
            (
                "consumer",
                "harbor",
                other.id,
                "stopped",
                json.dumps(data),
                json.dumps(
                    {"prerequisites": json.dumps([{"task": run.task_key, "run_id": run.id}])}
                ),
            ),
        )
    with pytest.raises(ApplicationError, match="retains this task's evidence"):
        delete(service)
    delete(service, other.id)
    assert not delete(service).cleanup_pending


def test_archiving_does_not_delete_inspection_data(tmp_path):
    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    inspection = Inspections(service.workspace).prepare(
        "harbor",
        InspectionPrepare(
            result_id=result.id,
            expected_revision=result.revision,
        ),
    )
    task = service.workspace.task("harbor", "work")
    service.workspace.edit_task(
        "harbor", "work", TaskEdit(expected_revision=task.revision, archived=True)
    )
    assert Path(inspection.workspace).exists()
    assert not delete(service).cleanup_pending
    assert not Path(inspection.workspace).exists()


def test_delete_preserves_selected_file_consumers_then_removes_retained_bytes(tmp_path):
    from flowfield.output_models import OutputSelection
    from flowfield.outputs import Outputs

    service, _, run = fixture(tmp_path)
    service.results.process("harbor")
    runtime = service.workspace.directory / "local-attempts" / run.id / "runtime"
    service.execution.save_local(run.id, {"runtime": str(runtime)})
    (runtime / "output" / "brief.txt").write_text("Keep this evidence")
    with service.workspace.connection(write=True) as db:
        output = Outputs(service.workspace).capture(db, run, [OutputSelection(path="brief.txt")])[0]
    file = Outputs(service.workspace).content(output)
    consumer = service.workspace.create_task(
        "harbor", TaskCreate(title="Read the brief", input_files=[output.id])
    )
    with pytest.raises(ApplicationError, match="uses this task's files"):
        delete(service)
    assert file.exists()
    delete(service, consumer.id)
    assert not delete(service).cleanup_pending
    assert not file.exists()


def test_cleanup_never_follows_links_outside_owned_store(tmp_path):
    from flowfield.task_deletion import CleanupPath

    service, _, _ = fixture(tmp_path)
    cleanup = TaskDeletionService(service.workspace)
    outside = tmp_path / "personal"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep")
    root = service.workspace.directory / "inspection-work" / "local-attempts"
    root.mkdir(parents=True)
    (root / "linked-copy").symlink_to(outside, target_is_directory=True)
    cleanup._remove_path(CleanupPath(relative="inspection-work/local-attempts/linked-copy"))
    assert marker.exists()
    assert not (root / "linked-copy").is_symlink()
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError, match="symlink"):
        cleanup._remove_path(CleanupPath(relative="inspection-work/local-attempts/keep"))
    assert marker.exists()


def test_delete_can_clean_owned_files_when_adopted_repository_was_removed(tmp_path):
    import shutil

    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    shutil.rmtree(repo)
    assert not delete(service).cleanup_pending
    assert not (service.workspace.directory / "local-attempts" / run.id).exists()


def test_pending_cleanup_resumes_from_saved_identity_after_restart(tmp_path, monkeypatch):
    from flowfield.application import Workspace

    service, _, run = fixture(tmp_path)
    service.results.process("harbor")
    original = TaskDeletionService._remove_path
    monkeypatch.setattr(
        TaskDeletionService, "_remove_path", lambda *_: (_ for _ in ()).throw(OSError("busy"))
    )
    assert delete(service).cleanup_pending
    monkeypatch.setattr(TaskDeletionService, "_remove_path", original)
    restarted = Workspace(service.workspace.directory)
    with restarted.connection(write=True) as db:
        db.execute("UPDATE task_deletions SET data=json_set(data,'$.retry_after',0)")
    TaskDeletionService(restarted).retry_cleanup()
    assert not (restarted.directory / "local-attempts" / run.id).exists()
    assert (
        not TaskDeletionService(restarted)
        .delete("harbor", "t-1", Edit(expected_revision=1))
        .cleanup_pending
    )


def test_background_cleanup_does_not_block_scheduling_and_shutdown_waits(tmp_path, monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock

    from flowfield.application import Workspace
    from flowfield.supervisor import Supervisor

    service = Supervisor(Workspace(tmp_path / "state"))
    entered, release = threading.Event(), threading.Event()

    def cleanup(_self):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(TaskDeletionService, "retry_cleanup", cleanup)
    review = AsyncMock()
    monkeypatch.setattr(service.review_execution, "schedule", review)

    async def exercise():
        try:
            await asyncio.wait_for(service._schedule_once(), 1)
            assert await asyncio.to_thread(entered.wait, 1)
            review.assert_awaited_once()
            assert service.cleanup_job and not service.cleanup_job.done()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0.05)
            assert not closing.done()
            release.set()
            await asyncio.wait_for(closing, 1)
        finally:
            release.set()

    asyncio.run(exercise())


def test_inspection_worktree_link_cannot_remove_adopted_repository(tmp_path):
    import shutil

    service, repo, _ = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    inspection = Inspections(service.workspace).prepare(
        "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
    )
    checkout = Path(inspection.workspace)
    shutil.rmtree(checkout)
    checkout.symlink_to(repo, target_is_directory=True)
    deleted = delete(service)
    assert not deleted.cleanup_pending
    assert (repo / "README.md").is_file()
    assert (repo / ".git").is_dir()
    assert not checkout.parent.exists()
