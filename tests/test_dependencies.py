"""Task graph validity, readiness and reconciliation across coordinated edits."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, existing_directory, reconcile_fixture_stages, task_request
from test_connection import running_service
from typer.testing import CliRunner

from flowfield.api import create_app
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskProgress,
    TaskReconcile,
    Workspace,
)
from flowfield.cli import app
from flowfield.errors import ApplicationError


def workspace(tmp_path: Path) -> Workspace:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    return service


def progress(service: Workspace, task: str, status: str):
    current = service.task("harbor", task)
    return service.record_progress(
        "harbor",
        task,
        TaskProgress(
            expected_revision=current.revision,
            status=status,
            completion="report",
            author="coordinator",
        ),
    )


def test_graph_validation_is_atomic_and_scoped(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    adopt(service, ProjectSetup(path=str(tmp_path / "other")))
    service.create_task("other", task_request(id="foreign", title="Foreign"))
    service.create_task("harbor", task_request(id="a", title="A"))
    service.create_task("harbor", task_request(id="b", title="B", dependencies=["a", "a"]))
    assert service.task("harbor", "b").dependencies == ["HAR-1"]
    before = service.board("harbor")
    for dependencies, code in [
        (["missing"], "invalid_dependency"),
        (["foreign"], "invalid_dependency"),
        (["a"], "invalid_dependency"),
        (["b"], "dependency_cycle"),
    ]:
        with pytest.raises(ApplicationError) as error:
            service.edit_task(
                "harbor",
                "a",
                TaskEdit(expected_revision=1, title="Lost", dependencies=dependencies),
            )
        assert error.value.code == code
        assert service.board("harbor") == before
    with pytest.raises(ApplicationError):
        service.create_task("harbor", task_request(id="self", title="Self", dependencies=["self"]))
    for status in ["in_progress", "in_review", "done"]:
        with pytest.raises(ApplicationError, match="prerequisites|Create the task"):
            service.create_task(
                "harbor",
                task_request(id="invalid", title="Invalid", status=status, dependencies=["a"]),
            )
        with pytest.raises(ApplicationError, match="Blocked by: A"):
            progress(service, "b", status)
    assert service.board("harbor") == before
    assert Workspace(tmp_path / "state").board("harbor") == before


def test_readiness_archive_and_downstream_reconciliation(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for key, dependencies in [("a", []), ("b", ["a"]), ("c", ["b"])]:
        service.create_task(
            "harbor",
            task_request(id=key, title=key.upper(), status="up_next", dependencies=dependencies),
        )
    service.edit_task("harbor", "a", TaskEdit(expected_revision=1, archived=True))
    assert service.task("harbor", "b").blocked_by[0].archived
    service.edit_task("harbor", "a", TaskEdit(expected_revision=2, archived=False))
    progress(service, "a", "done")
    assert service.task("harbor", "b").readiness == "draft"
    assert service.task("harbor", "b").revision == 1  # Derived readiness, no fictitious edit.
    for key in ["b", "c"]:
        progress(service, key, "done")
    # Archived completed work still satisfies its prerequisites.
    current = service.task("harbor", "a")
    service.edit_task("harbor", "a", TaskEdit(expected_revision=current.revision, archived=True))
    assert service.task("harbor", "b").readiness == "ready"
    service.edit_task(
        "harbor", "a", TaskEdit(expected_revision=current.revision + 1, archived=False)
    )
    entered = service.task("harbor", "b").status_changed_at
    progress(service, "a", "in_progress")
    for key in ["b", "c"]:
        affected = service.task("harbor", key)
        assert affected.status == "done" and affected.readiness == "needs_reconciliation"
        assert affected.updated_by == "coordinator"
        assert affected.revisions[-1].change_note
        with pytest.raises(ApplicationError):
            service.reconcile_task(
                "harbor", key, TaskReconcile(expected_revision=affected.revision, note="Reviewed")
            )
    assert service.task("harbor", "b").status_changed_at == entered
    progress(service, "a", "done")
    with pytest.raises(ApplicationError, match="reconcile"):
        progress(service, "b", "done")  # Even a same-status completion must not bypass review.
    for key in ["b", "c"]:
        current = service.task("harbor", key)
        result = service.reconcile_task(
            "harbor",
            key,
            TaskReconcile(
                expected_revision=current.revision,
                note="Reviewed output against the corrected prerequisite.",
                author="coordinator",
            ),
        )
        assert result.readiness == "ready" and result.status == "done"
        assert result.change_note.startswith("Reviewed output")
    assert Workspace(tmp_path / "state").board("harbor") == service.board("harbor")


def test_added_dependency_preserves_work_and_explicit_replanning(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for key in ["a", "b"]:
        service.create_task("harbor", task_request(id=key, title=key, status="in_progress"))
    changed = service.edit_task("harbor", "b", TaskEdit(expected_revision=1, dependencies=["a"]))
    assert changed.revision == 2 and changed.status == "in_progress"
    assert changed.reconciliation_reason
    cleared = service.edit_task("harbor", "b", TaskEdit(expected_revision=2, dependencies=[]))
    assert cleared.reconciliation_reason  # Removing a gate is not a review of recorded work.
    replanned = progress(service, "b", "up_next")
    assert replanned.reconciliation_reason is None
    assert "withdrawn" in replanned.change_note
    service.edit_task(
        "harbor", "b", TaskEdit(expected_revision=replanned.revision, dependencies=["a"])
    )
    reconcile_fixture_stages(service, "harbor", "b")
    assert service.task("harbor", "b").readiness == "blocked"


def test_concurrent_edges_and_completion_are_serialized(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for key in ["a", "b"]:
        service.create_task("harbor", task_request(id=key, title=key))

    def link(key: str):
        try:
            return service.edit_task(
                "harbor",
                key,
                TaskEdit(expected_revision=1, dependencies=["b" if key == "a" else "a"]),
            )
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(link, ["a", "b"]))
    assert sum(result == "dependency_cycle" for result in results) == 1
    linked = next(task for task in service.tasks("harbor") if task.dependencies)
    prerequisite = linked.dependencies[0]
    progress(service, prerequisite, "done")

    def race(action: str):
        try:
            return progress(
                service,
                prerequisite if action == "reopen" else linked.id,
                "up_next" if action == "reopen" else "done",
            )
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(race, ["reopen", "complete"]))
    final = service.task("harbor", linked.id)
    assert final.blocked_by
    assert final.status == "backlog" or (final.status == "done" and final.reconciliation_reason)


def test_http_reconciliation_and_cli_dependencies(tmp_path: Path) -> None:
    runner = CliRunner()
    with running_service(tmp_path / "state") as base:
        args = ["--data-dir", str(tmp_path / "state"), "--port", base.rsplit(":", 1)[1]]

        def cli(*command: str):
            result = runner.invoke(app, args + list(command))
            assert result.exit_code == 0, result.output
            return result.output

        space = json.loads(cli("workspace", "create", "Product", "--json"))
        cli(
            "project",
            "init",
            existing_directory(str(tmp_path / "harbor")),
            "--workspace",
            space["id"],
        )
        cli("task", "create", "--project", "harbor", "--id", "a", "--title", "Serializer")
        cli(
            "task",
            "create",
            "--project",
            "harbor",
            "--id",
            "b",
            "--title",
            "Download",
            "--depends-on",
            "a",
        )
        assert "Blocked by: HAR-1 (Serializer)" in cli(
            "task", "show", "HAR-2", "--project", "harbor"
        )
        cli("task", "edit", "b", "--project", "harbor", "--no-dependencies")
        cli("task", "edit", "b", "--project", "harbor", "--depends-on", "a", "--depends-on", "a")
        cli("task", "progress", "HAR-1", "done", "--completion", "report", "--project", "harbor")
        cli("task", "progress", "HAR-2", "done", "--completion", "report", "--project", "harbor")
        cli("task", "progress", "HAR-1", "up-next", "--project", "harbor")
        cli("task", "progress", "HAR-1", "done", "--completion", "report", "--project", "harbor")
        cli("task", "reconcile", "b", "--project", "harbor", "--note", "Reviewed the integration.")
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        task = client.get("/api/projects/harbor/tasks/b").json()
        assert task["readiness"] == "ready" and task["change_note"] == "Reviewed the integration."
        assert task["dependencies"] == ["HAR-1"]
        assert (
            client.post(
                "/api/projects/harbor/tasks/b/reconcile",
                json={"expected_revision": task["revision"], "note": " "},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/projects/harbor/tasks/b/reconcile",
                json={"expected_revision": 1, "note": "Stale"},
            ).status_code
            == 409
        )
