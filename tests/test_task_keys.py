"""Stable human references across concurrent creation, edits and shared interfaces."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from project_fixtures import adopt, existing_directory, task_request

from flowfield.application import (
    ProjectEdit,
    ProjectSetup,
    TaskEdit,
    TaskPriority,
    TaskProgress,
    TaskReconcile,
    Workspace,
)
from flowfield.errors import ApplicationError


def test_keys_are_unique_stable_and_never_reused(tmp_path: Path) -> None:
    service = Workspace(tmp_path / "state")
    for identity in ["harbor", "hardware", "123"]:
        adopt(
            service,
            ProjectSetup(
                path=str(tmp_path / identity), task_prefix="HWD" if identity == "hardware" else None
            ),
        )
    assert [p.task_prefix for p in service.projects()] == ["PRJ", "HAR", "HWD"]

    def create(number: int):
        return service.create_task("harbor", task_request(title=f"Task {number}"))

    with ThreadPoolExecutor(4) as pool:
        tasks = list(pool.map(create, range(12)))
    assert {t.key for t in tasks} == {f"HAR-{n}" for n in range(1, 13)}
    task = service.task("harbor", "HAR-1")
    service.edit_task(
        "harbor",
        task.key,
        TaskEdit(expected_revision=1, title="Renamed", task_type="bug", archived=True),
    )
    service.edit_project("harbor", ProjectEdit(expected_revision=1, name="New project name"))
    assert service.create_task("harbor", task_request(title="Next")).key == "HAR-13"
    assert service.task("harbor", task.id).key == task.key
    assert {r.key for r in service.task("harbor", task.key).revisions} == {"HAR-1"}
    assert service.setup_project(ProjectSetup(path=str(tmp_path / "harbor"))).task_prefix == "HAR"
    assert service.create_task("hardware", task_request(title="Other")).key == "HWD-1"
    with pytest.raises(ApplicationError, match="not found"):
        service.task("hardware", "HAR-1")
    assert Workspace(tmp_path / "state").board("harbor") == service.board("harbor")


def test_key_aliases_share_graph_progress_and_revision_checks(tmp_path: Path) -> None:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    a = service.create_task("harbor", task_request(id="serializer", title="Serializer"))
    assert service.task("harbor", a.key.lower()).id == a.id
    assert service.task("harbor", "hAr-1").id == a.id
    assert service.activity("harbor", task_id="har-1").items
    b = service.create_task(
        "harbor", task_request(id="download", title="Download", dependencies=[a.key.lower(), a.id])
    )
    assert b.dependencies == [a.id] and b.blocked_by[0].key == a.key
    assert service.task("harbor", a.key).dependents[0].key == b.key
    assert (
        service.edit_task(
            "harbor", b.key.lower(), TaskEdit(expected_revision=1, dependencies=[a.key])
        )
        == b
    )
    with pytest.raises(ApplicationError, match="cycle"):
        service.edit_task("harbor", a.key, TaskEdit(expected_revision=1, dependencies=[b.key]))
    with pytest.raises(ApplicationError, match="stale"):
        service.edit_task("harbor", b.key, TaskEdit(expected_revision=2, title="Lost"))
    prioritized = service.prioritize_task(
        "harbor", b.key, TaskPriority(expected_revision=1, status="backlog", before_id=a.key)
    )
    assert service.tasks("harbor")[0].id == b.id
    service.record_progress(
        "harbor", a.key, TaskProgress(expected_revision=1, status="done", completion="report")
    )
    done = service.record_progress(
        "harbor",
        b.key,
        TaskProgress(expected_revision=prioritized.revision, status="done", completion="report"),
    )
    service.record_progress("harbor", a.key, TaskProgress(expected_revision=2, status="up_next"))
    service.record_progress(
        "harbor", a.key, TaskProgress(expected_revision=3, status="done", completion="report")
    )
    reviewed = service.reconcile_task(
        "harbor",
        b.key,
        TaskReconcile(expected_revision=done.revision + 1, note="Reviewed changed prerequisite."),
    )
    assert reviewed.readiness == "ready" and reviewed.key == b.key


def test_configurable_prefix_validation_and_atomic_lock(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from flowfield.api import create_app

    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        for prefix in ["A", "AB", "ABCD", "A12", "ÅBC"]:
            response = client.post(
                "/api/projects/initialize",
                json={"path": existing_directory(str(tmp_path / "invalid")), "task_prefix": prefix},
            )
            assert response.status_code == 422
            assert not (tmp_path / "invalid" / ".flowfield").exists()
        project = client.post(
            "/api/projects/initialize",
            json={"path": existing_directory(str(tmp_path / "harbor")), "task_prefix": "csv"},
        ).json()
        assert project["task_prefix"] == "CSV"
        collision = client.post(
            "/api/projects/initialize",
            json={"path": existing_directory(str(tmp_path / "other")), "task_prefix": "CSV"},
        )
        assert collision.status_code == 409 and not (tmp_path / "other" / ".flowfield").exists()
        renamed = client.put(
            "/api/projects/harbor", json={"expected_revision": 1, "task_prefix": "har"}
        ).json()
        assert renamed["task_prefix"] == "HAR" and renamed["revision"] == 2
        assert (
            client.post(
                "/api/projects/initialize",
                json={"path": str(tmp_path / "harbor"), "task_prefix": "HAR"},
            ).json()
            == renamed
        )
        other = client.post(
            "/api/projects/initialize",
            json={"path": existing_directory(str(tmp_path / "hardware")), "task_prefix": "HWD"},
        ).json()
        rejected = client.put(
            "/api/projects/harbor",
            json={"expected_revision": 2, "task_prefix": "HWD", "name": "Lost"},
        )
        assert rejected.status_code == 409
        assert client.get("/api/projects/harbor").json() == renamed
        task = client.post(
            "/api/projects/harbor/tasks",
            json={
                "stages": task_request(title="Fixture").model_dump()["stages"],
                "title": "First task",
            },
        ).json()
        client.put(
            f"/api/projects/harbor/tasks/{task['key']}",
            json={"expected_revision": 1, "archived": True},
        )
        locked = client.put(
            "/api/projects/harbor",
            json={"expected_revision": 2, "task_prefix": "CSV", "name": "Lost"},
        )
        assert locked.status_code == 409 and locked.json()["error"]["code"] == "prefix_locked"
        assert client.get("/api/projects/harbor").json() == renamed
        assert client.get(f"/api/projects/harbor/tasks/{task['key']}").json()["key"] == "HAR-1"
        # Short automatic prefixes still contain exactly three letters.
        short = client.post(
            "/api/projects/initialize", json={"path": existing_directory(str(tmp_path / "ab"))}
        ).json()
        assert short["task_prefix"] == "ABX"
        assert other["task_prefix"] == "HWD"


def test_case_insensitive_keys_keep_exact_ids_and_project_scope(tmp_path):
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    adopt(service, ProjectSetup(path=str(tmp_path / "other")))
    first = service.create_task("harbor", task_request(id="first", title="First"))
    exact = service.create_task("harbor", task_request(id="har-1", title="Exact ID"))
    assert service.task("harbor", "HAR-1").id == first.id
    assert service.task("harbor", "har-1").id == exact.id
    assert service.task("harbor", "hAr-1").id == first.id
    with pytest.raises(ApplicationError, match="not found"):
        service.task("other", "hAr-1")
    task = service.create_task(
        "harbor", task_request(id="dependent", title="Dependent", dependencies=["har-1"])
    )
    assert task.dependencies == [exact.id]
