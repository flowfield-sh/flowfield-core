"""Activity is durable evidence, separate from current task agreement and progress."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, existing_directory, task_request

from flowfield.activity import ActivityCreate
from flowfield.api import create_app
from flowfield.application import (
    ProjectSetup,
    TaskEdit,
    TaskPriority,
    TaskProgress,
    Workspace,
)
from flowfield.errors import ApplicationError


def workspace(tmp_path: Path) -> Workspace:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    service.create_task("harbor", task_request(id="one", title="Export CSV"))
    service.create_task("harbor", task_request(id="two", title="Download CSV"))
    return service


def test_activity_does_not_change_agreement_and_important_events_are_atomic(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    original = service.task("harbor", "one")
    note = ActivityCreate(id="retry-note", task_id="HAR-1", body="**Finding:** reuse serializer.")
    entry = service.add_activity("harbor", note)
    assert service.add_activity("harbor", note) == entry
    with pytest.raises(ApplicationError, match="already used"):
        service.add_activity("harbor", note.model_copy(update={"body": "Different"}))
    task = service.task("harbor", "one")
    assert task.latest_update == entry
    assert task.revisions == original.revisions and task.updated_at == original.updated_at
    assert task.body == original.body and task.status == original.status
    service.edit_task("harbor", "one", TaskEdit(expected_revision=1, body="Agreed requirement"))
    service.prioritize_task("harbor", "one", TaskPriority(expected_revision=2, status="backlog"))
    # Description edits are recorded; ordering within a column is not.
    assert len(service.activity("harbor", task_id="one").items) == 3
    assert service.activity("harbor", task_id="one").items[0].body == "Description updated."
    service.record_progress(
        "harbor", "one", TaskProgress(expected_revision=3, status="in_progress")
    )
    assert service.activity("harbor", task_id="one").items[0].body == "Backlog → In progress."
    snapshot = service.activity("harbor", task_id="one")
    with pytest.raises(ApplicationError):
        service.edit_task("harbor", "one", TaskEdit(expected_revision=4, dependencies=["missing"]))
    assert service.activity("harbor", task_id="one") == snapshot
    service.record_progress(
        "harbor", "one", TaskProgress(expected_revision=4, status="done", completion="report")
    )
    service.edit_task("harbor", "one", TaskEdit(expected_revision=5, archived=True))
    service.add_activity(
        "harbor", ActivityCreate(task_id="one", body="Archived result remains useful.")
    )
    assert service.activity("harbor", task_id="one").items[1].body == "Task archived."
    assert Workspace(tmp_path / "state").activity("harbor", task_id="HAR-1") == service.activity(
        "harbor", task_id="one"
    )


def test_activity_pagination_is_stable_across_new_entries(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    for number in range(5):
        service.add_activity("harbor", ActivityCreate(task_id="one", body=f"Finding {number}"))
    first = service.activity("harbor", task_id="one", limit=2)
    assert first.next_cursor
    service.add_activity("harbor", ActivityCreate(task_id="one", body="New while reading"))
    entries = first.items[:]
    cursor = first.next_cursor
    while cursor:
        page = service.activity("harbor", task_id="one", limit=2, before=cursor)
        entries.extend(page.items)
        cursor = page.next_cursor
    assert len(entries) == 6 and len({entry.id for entry in entries}) == 6
    assert [entry.body for entry in entries[:2]] == ["Finding 4", "Finding 3"]
    assert not any(entry.body == "New while reading" for entry in entries)
    with pytest.raises(ApplicationError):
        service.activity("harbor", limit=101)


def test_http_activity_validates_and_preserves_scope(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        space = client.post("/api/workspaces", json={"name": "Fixture"}).json()["id"]
        client.post(
            f"/api/workspaces/{space}/projects",
            json={"path": existing_directory(str(tmp_path / "harbor"))},
        )
        client.post(
            "/api/projects/harbor/tasks",
            json={"title": "CSV"},
        )
        endpoint = "/api/projects/harbor/activity"
        for entry in [
            {"body": "project note"},
            {"task_id": "HAR-1", "body": "  "},
            {"task_id": "HAR-1", "kind": "event", "body": "Fake completion"},
            {"task_id": "HAR-1", "body": "Note", "supersedes": "old"},
        ]:
            assert client.post(endpoint, json=entry).status_code == 422
        response = client.post(
            endpoint,
            json={
                "task_id": "HAR-1",
                "kind": "note",
                "body": "# Export\n\nInclude **all columns**.",
            },
        )
        assert response.status_code == 201
        entry = response.json()
        assert client.get(endpoint + "/" + entry["id"]).json() == entry
        assert client.get(endpoint, params={"task_id": "HAR-1", "current_only": True}).json()[
            "items"
        ] == [entry]
        assert client.get(endpoint).json()["items"] == []
        assert client.put(endpoint + "/" + entry["id"], json={"body": "rewrite"}).status_code == 404


def test_edit_events_record_changes_but_not_noops(tmp_path: Path) -> None:
    service = workspace(tmp_path)
    task = service.create_task("harbor", task_request(title="Next", status="up_next"))
    assert service.activity("harbor", task_id=task.id).items[0].body == "Task created in Up next."
    task = service.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=1, body="Done when clear.")
    )
    service.edit_task("harbor", task.id, TaskEdit(expected_revision=2, body=task.body))
    events = service.activity("harbor", task_id=task.id).items
    assert len(events) == 2 and events[0].task_revision == 2
    service.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=2, title="Clear next step", labels=["investigation"]),
    )
    assert (
        service.activity("harbor", task_id=task.id).items[0].body
        == "Title updated.\n\nLabels updated."
    )
