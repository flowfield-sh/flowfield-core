"""Context budgets protect a fresh agent from project/history growth."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lifecycle_fixtures import run_report
from project_fixtures import adopt, existing_directory, task_request
from workspace_fixtures import TEST_AGENTS

from flowfield.activity import ActivityCreate
from flowfield.api import create_app
from flowfield.application import ProjectEdit, ProjectSetup, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.questions import QuestionCreate, Questions
from flowfield.reads import PAGE_BYTES, ContextReads, receipt, size


def test_context_growth_pagination_and_full_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    text = 'A🦉\\"' * 2000
    for i in range(16):
        service.create_task(
            "harbor",
            task_request(
                id=f"t{i}",
                title=f"Task {i}",
                body=text,
                labels=["bug" if i % 2 else "feature"],
            ),
        )
    service.edit_task(
        "harbor", "t15", TaskEdit(expected_revision=1, dependencies=[f"t{i}" for i in range(12)])
    )
    for _ in range(25):
        service.add_activity(
            "harbor", ActivityCreate(task_id="t15", kind="note", body='🦉\\"' * 50000)
        )
    reads = ContextReads(service)
    # Context reads must not silently route through the old full-board/task loaders.
    monkeypatch.setattr(service, "_task", lambda *a, **kw: pytest.fail("hydrated full task"))
    monkeypatch.setattr(service, "_tasks", lambda *a, **kw: pytest.fail("hydrated full board"))
    overview = reads.overview("harbor")
    assert overview["columns"][0]["count"] == 16
    assert len(overview["columns"][0]["tasks"]) == 3 and overview["columns"][0]["has_more"]
    assert "body" not in overview["columns"][0]["tasks"][0]
    detail = reads.task("harbor", "T-16")
    assert "revisions" not in detail and detail["prerequisites_count"] == 12
    assert len(detail["prerequisites"]) <= 5 and detail["blocked_by_count"] == 12
    assert detail["truncated_fields"]["body"]["total_characters"] == len(text)
    assert detail["latest_update"]["truncated_fields"]["body"]
    assert detail["blocked_by_keys"] == ["T-1", "T-10", "T-11"]
    assert detail["blocked_count"] == 12
    for result in [
        overview,
        detail,
        reads.tasks("harbor", limit=50),
        reads.activity("harbor", task_id="t15", limit=50),
    ]:
        assert size(result) <= PAGE_BYTES
    activities, before = [], None
    while True:
        result = reads.activity("harbor", task_id="t15", kind="note", limit=50, before=before)
        assert size(result) <= PAGE_BYTES
        activities += [e["id"] for e in result["items"]]
        before = result["next_cursor"]
        if before is None:
            break
    assert len(activities) == len(set(activities)) == 25
    prerequisites, after = [], None
    while True:
        result = reads.relationships("harbor", "t15", after=after, limit=3)
        prerequisites += [t["id"] for t in result["items"]]
        after = result["next_cursor"]
        if after is None:
            break
    assert set(prerequisites) == {f"t{i}" for i in range(12)}
    collected, offset = "", 0
    while True:
        result = reads.text("harbor", "task", "t15", revision=1, offset=offset)
        assert size(result) <= PAGE_BYTES
        collected += result["text"]
        offset = result["next_offset"]
        if offset is None:
            break
    assert collected == text
    page = reads.tasks("harbor", label_id="bug", limit=2)
    assert [t["key"] for t in page["items"]] == ["T-2", "T-4"]
    assert [
        t["key"]
        for t in reads.tasks("harbor", label_id="bug", after=page["next_cursor"], limit=2)["items"]
    ] == ["T-6", "T-8"]
    assert reads.tasks("harbor", query="T-16")["items"][0]["readiness"] == "blocked"
    revisions = reads.revisions("harbor", "t15", limit=1)
    assert revisions["items"][0]["revision"] == 2 and revisions["next_cursor"] == 2
    assert "body" not in revisions["items"][0]
    assert reads.revisions("harbor", "t15", before=2)["items"][0]["revision"] == 1


def test_context_readiness_matches_domain_and_invalid_input(tmp_path: Path) -> None:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    service.create_task("harbor", task_request(id="one", title="One"))
    run_report(service, "harbor", "one", expected_revision=1, status="done", completion="report")
    service.create_task("harbor", task_request(id="two", title="Two", dependencies=["one"]))
    run_report(service, "harbor", "two", expected_revision=1, status="done", completion="report")
    service.create_task("harbor", task_request(id="three", title="Three", dependencies=["two"]))
    reads = ContextReads(service)
    for status in ["backlog", "done"]:
        run_report(
            service,
            "harbor",
            "one",
            expected_revision=service.task("harbor", "one").revision,
            status=status,
            completion="report",
        )
        for task in service.tasks("harbor"):
            detail = reads.task("harbor", task.key)
            assert detail["readiness"] == task.readiness
            assert {p["id"] for p in detail["blocked_by"]} == {p.id for p in task.blocked_by}
    for kwargs in [{"limit": 51}, {"after": -1}, {"status": "invalid"}]:
        with pytest.raises(ApplicationError):
            reads.tasks("harbor", **kwargs)
    first = reads.text("harbor", "project", identity="harbor", field="description")
    service.edit_project("harbor", ProjectEdit(expected_revision=1, description="Changed"))
    with pytest.raises(ApplicationError, match="changed"):
        reads.text(
            "harbor", "project", identity="harbor", field="description", revision=first["revision"]
        )
    ack = receipt(service.task("harbor", "one").model_dump())
    assert ack["saved"] and ack["key"] == "T-1" and "revisions" not in ack and "body" not in ack


def test_http_context_is_scoped_and_bounded(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        space = client.post(
            "/api/workspaces",
            json={
                **{role: choice.model_dump() for role, choice in TEST_AGENTS.items()},
                "name": "Fixture",
            },
        ).json()["id"]
        client.post(
            f"/api/workspaces/{space}/projects",
            json={"path": existing_directory(str(tmp_path / "harbor"))},
        )
        client.post(
            f"/api/workspaces/{space}/tasks",
            json={
                "title": "Export",
                "body": "x" * 200000,
            },
        )
        base = f"/api/context/workspaces/{space}"
        assert "revisions" not in client.get(base + "/tasks/T-1").json()
        assert len(client.get(base + "/tasks/T-1").content) < PAGE_BYTES
        matches = client.get(base + "/search", params={"query": "Export", "entity": "task"})
        assert matches.status_code == 200
        assert matches.json()["items"][0]["current"]
        assert matches.json()["items"][0]["source"]["resource"] == "task"
        assert client.get(base + "/search", params={"query": "*"}).status_code == 400
        assert (
            client.get(base + "/search", params={"query": "Export", "limit": 0}).status_code == 400
        )
        assert client.get(base + "/tasks", params={"limit": 0}).status_code == 400
        assert client.get("/api/context/projects/missing/tasks/T-1").status_code == 404
        assert (
            client.get(
                base + "/text", params={"resource": "task", "identity": "T-1", "field": "invalid"}
            ).status_code
            == 400
        )
        assert (
            client.get(
                base + "/tasks/T-1/relationships", params={"relation": "invalid"}
            ).status_code
            == 422
        )


def test_browser_links_and_question_pagination(tmp_path: Path) -> None:
    service = Workspace(tmp_path / "state")
    adopt(service, ProjectSetup(path=str(tmp_path / "harbor")))
    service.create_task("harbor", task_request(id="catalog", title="Catalog", status="up_next"))
    for i in range(20):
        Questions(service).ask(
            "harbor",
            QuestionCreate(
                id=f"q{i}",
                question="Which option?" * 10,
                context="Context" * 1000,
                recommendation="Choose the first option.",
                blocking_scope="Scope" * 100,
                task_id="catalog",
            ),
        )
    origin = "http://127.0.0.1:8912"
    reads = ContextReads(service, origin)
    assert reads.project("harbor")["url"] == origin + "/projects/harbor"
    assert reads.task("harbor", "catalog")["url"] == origin + "/workspaces/harbor/tasks/T-1"
    assert reads.overview("harbor")["attention"]["open"]["items"][0]["url"] == (
        origin + "/workspaces/harbor/inbox/q0"
    )
    assert reads.question("harbor", "q0")["url"] == origin + "/workspaces/harbor/inbox/q0"
    seen, after = [], None
    while True:
        result = reads.questions("harbor", after=after, limit=50)
        assert size(result) <= PAGE_BYTES
        for item in result["items"]:
            assert item["url"] == origin + "/workspaces/harbor/inbox/" + item["id"]
            seen.append(item["id"])
        after = result["next_cursor"]
        if after is None:
            break
    assert len(seen) == len(set(seen)) == 20
    assert reads.url("harbor", "inbox", "a/b ?") == origin + "/workspaces/harbor/inbox/a%2Fb%20%3F"
