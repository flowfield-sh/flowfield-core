"""Task graph validity, readiness and reconciliation across coordinated edits."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lifecycle_fixtures import run_report
from project_fixtures import adopt, existing_directory, task_request
from workspace_fixtures import TEST_AGENTS

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskEdit, TaskReconcile, Workspace
from flowfield.browser import BrowserReads
from flowfield.errors import ApplicationError


def workspace(tmp_path: Path) -> Workspace:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    return service


def progress(service: Workspace, task: str, status: str):
    current = service.task("harbor", task)
    return run_report(
        service,
        "harbor",
        task,
        expected_revision=current.revision,
        status=status,
        completion="report",
        author="coordinator",
    )


def test_graph_validation_is_atomic_and_scoped(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    adopt(service, ProjectSetup(path=str(tmp_path / "other")))
    service.create_task("other", task_request(id="foreign", title="Foreign"))
    service.create_task("harbor", task_request(id="a", title="A"))
    service.create_task("harbor", task_request(id="b", title="B", dependencies=["a", "a"]))
    assert service.task("harbor", "b").dependencies == ["T-1"]
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


def test_completed_deliveries_remain_done_without_cascades(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for key, dependencies in [("a", []), ("b", ["a"]), ("c", ["b"])]:
        service.create_task(
            "harbor",
            task_request(id=key, title=key.upper(), status="up_next", dependencies=dependencies),
        )
    with pytest.raises(ApplicationError, match="prerequisite"):
        service.edit_task("harbor", "a", TaskEdit(expected_revision=1, archived=True))
    progress(service, "a", "done")
    assert service.task("harbor", "b").readiness == "blocked"
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
    saved = {key: service.task("harbor", key) for key in ["b", "c"]}
    with service.connection() as db:
        results = db.execute("SELECT data FROM result_versions ORDER BY id").fetchall()
    # A new consumer can use B without changing completed work.
    service.create_task("harbor", task_request(id="d", title="New outcome", dependencies=["b"]))
    with pytest.raises(ApplicationError, match="Done is permanent"):
        progress(service, "a", "in_progress")
    for key in ["b", "c"]:
        retained = service.task("harbor", key)
        assert retained.status == "done" and retained.readiness == "ready"
        assert not retained.blocked_by and retained.reconciliation_reason is None
        assert retained.revisions == saved[key].revisions
        assert retained.status_changed_at == saved[key].status_changed_at
        assert retained.publication == saved[key].publication
        state = BrowserReads(service).task("harbor", key).state
        assert state.label == "Done" and state.tone == "complete"
        assert state.reason is None
        card = next(t for t in BrowserReads(service).workspace_board("harbor").tasks if t.id == key)
        assert not card.blocked_by and card.readiness == "ready"
        with pytest.raises(ApplicationError, match="does not need reconciliation"):
            service.reconcile_task(
                "harbor", key, TaskReconcile(expected_revision=retained.revision, note="Reviewed")
            )
    with service.connection() as db:
        retained_results = db.execute("SELECT data FROM result_versions ORDER BY id").fetchall()
    assert [row[0] for row in retained_results] == [row[0] for row in results]
    assert not service.task("harbor", "d").blocked_by
    progress(service, "d", "done")
    progress(service, "a", "done")
    for key in ["b", "c"]:
        assert service.task("harbor", key).revisions == saved[key].revisions
    with pytest.raises(ApplicationError, match="Done is permanent"):
        progress(service, "b", "in_progress")
    assert service.task("harbor", "c").revisions == saved["c"].revisions
    assert service.task("harbor", "c").readiness == "ready"
    assert Workspace(tmp_path / "state").board("harbor") == service.board("harbor")


def test_added_dependency_preserves_work_and_explicit_replanning(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for key in ["a", "b"]:
        service.create_task(
            "harbor",
            task_request(
                id=key, title=key, body="Review the dependency graph", status="in_progress"
            ),
        )
    changed = service.edit_task("harbor", "b", TaskEdit(expected_revision=1, dependencies=["a"]))
    assert changed.revision == 2 and changed.status == "in_progress"
    assert changed.reconciliation_reason
    cleared = service.edit_task("harbor", "b", TaskEdit(expected_revision=2, dependencies=[]))
    assert cleared.reconciliation_reason  # Removing a gate is not a review of recorded work.
    replanned = service.reconcile_task(
        "harbor",
        "b",
        TaskReconcile(
            expected_revision=cleared.revision,
            note="Reviewed revised dependencies",
            completion="report",
        ),
    )
    assert replanned.reconciliation_reason is None
    service.edit_task(
        "harbor", "b", TaskEdit(expected_revision=replanned.revision, dependencies=["a"])
    )
    assert service.task("harbor", "b").readiness == "needs_reconciliation"


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
                prerequisite if action == "move" else linked.id,
                "up_next" if action == "move" else "done",
            )
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(race, ["move", "complete"]))
    assert results[0] == "task_completed"
    final = service.task("harbor", linked.id)
    assert final.status == "done" and final.readiness == "ready"
    assert not final.blocked_by and not final.reconciliation_reason


def test_http_reconciliation_and_dependencies(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        space = client.post(
            "/api/workspaces",
            json={
                "name": "Product",
                **{role: choice.model_dump() for role, choice in TEST_AGENTS.items()},
            },
        ).json()
        base = f"/api/workspaces/{space['id']}"
        assert (
            client.post(
                base + "/projects", json={"path": existing_directory(str(tmp_path / "harbor"))}
            ).status_code
            == 201
        )
        assert (
            client.post(base + "/tasks", json={"id": "a", "title": "Serializer"}).status_code == 201
        )
        created = client.post(
            base + "/tasks", json={"id": "b", "title": "Download", "dependencies": ["a"]}
        ).json()
        assert created["blocked_by"][0]["key"] == "T-1"
        cleared = client.put(
            base + "/tasks/b", json={"expected_revision": 1, "dependencies": []}
        ).json()
        assert not cleared["dependencies"]
        assert (
            client.put(
                base + "/tasks/b",
                json={"expected_revision": cleared["revision"], "dependencies": ["a", "a"]},
            ).status_code
            == 200
        )
        store = Workspace(tmp_path / "state")
        for key in ("a", "b"):
            current = store.task(space["id"], key)
            run_report(store, space["id"], key, expected_revision=current.revision, status="done")
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://127.0.0.1") as client:
        task = client.get(f"/api/workspaces/{space['id']}/tasks/b").json()
        assert task["readiness"] == "ready" and task["status"] == "done"
        assert task["dependencies"] == ["T-1"]
        assert (
            client.post(
                f"/api/workspaces/{space['id']}/tasks/b/reconcile",
                json={"expected_revision": task["revision"], "note": " "},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/workspaces/{space['id']}/tasks/b/reconcile",
                json={"expected_revision": 1, "note": "Stale"},
            ).status_code
            == 409
        )
