"""Classifications are shared metadata, without worker or approval side effects."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from execution_fixtures import BASE, fixture
from fastapi.testclient import TestClient
from project_fixtures import existing_directory, task_request
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.agent_tools import workspace_scope
from flowfield.api import create_app
from flowfield.application import Edit, ProjectSetup, TaskEdit, Workspace
from flowfield.errors import ApplicationError
from flowfield.labels import LabelCreate, LabelEdit, Labels
from flowfield.migrations import Migration
from flowfield.reads import PAGE_BYTES, ContextReads, size
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_optional_shared_labels_preserve_running_assignment_and_history(tmp_path):
    execution = fixture(tmp_path)
    store = execution.workspace
    spaces = Workspaces(store)
    spaces.adopt("harbor", ProjectSetup(path=existing_directory(tmp_path / "site")))
    labels = Labels(store)
    custom = labels.create(
        "harbor", LabelCreate(name="Release", color="green", guidance="Shipping work")
    )
    task = store.task("harbor", "task-0")
    tagged = store.edit_task(
        "harbor",
        task.id,
        TaskEdit(expected_revision=task.revision, labels=["bug", custom.id, "bug"]),
    )
    assert tagged.labels == sorted(["bug", custom.id])
    assert tagged.agreement_revision == task.agreement_revision
    assert tagged.publication == task.publication
    sibling = store.create_task("site", task_request(title="Release site", labels=[custom.id]))
    assert store.create_task("site", task_request(title="Unclassified")).labels == []
    run = execution.claim("harbor", BASE, {BASE: set()})
    original_assignment = execution.assignment("harbor", run.id)
    changed = labels.edit("harbor", custom.id, LabelEdit(expected_revision=1, name="Launch"))
    assert changed.name == "Launch" and changed.revision == 2
    with pytest.raises(ApplicationError, match="stale"):
        labels.edit("harbor", custom.id, LabelEdit(expected_revision=1, name="Stale"))
    with pytest.raises(ApplicationError, match="already exists"):
        labels.create("harbor", LabelCreate(name="launch"))
    deleted = labels.delete("harbor", custom.id, Edit(expected_revision=2))
    assert deleted.deleted_at and deleted.revision == 3
    current = store.task("harbor", task.id)
    assert current.labels == ["bug"] and current.status == "in_progress"
    assert current.agreement_revision == task.agreement_revision
    assert current.publication == task.publication
    assert current.reconciliation_reason is None
    assert store.task("site", sibling.id).labels == []
    assert any(custom.id in revision.labels for revision in current.revisions)
    assert execution.assignment("harbor", run.id) == original_assignment
    assert labels.page("harbor")["items"] and all(
        v["id"] != custom.id for v in labels.page("harbor")["items"]
    )
    with pytest.raises(ApplicationError, match="existing workspace label"):
        store.edit_task(
            "site", sibling.id, TaskEdit(expected_revision=sibling.revision + 1, labels=[custom.id])
        )
    assert labels.create("harbor", LabelCreate(name="Launch")).id != custom.id
    assert [v["id"] for v in ContextReads(store).tasks("harbor", label_id="bug")["items"]] == [
        task.id
    ]


def test_label_scope_and_assign_delete_race(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    first = spaces.create(WorkspaceCreate(name="First"))
    second = spaces.create(WorkspaceCreate(name="Second"))
    spaces.adopt(first.id, ProjectSetup(path=existing_directory(tmp_path / "core")))
    labels = Labels(store)
    foreign = labels.create(second.id, LabelCreate(name="Private"))
    task = store.create_task("core", task_request(title="Task"))
    with pytest.raises(ApplicationError, match="not found"):
        store.edit_task("core", task.id, TaskEdit(expected_revision=1, labels=[foreign.id]))
    custom = labels.create(first.id, LabelCreate(name="Race"))

    def assign():
        try:
            return store.edit_task(
                "core", task.id, TaskEdit(expected_revision=1, labels=[custom.id])
            )
        except ApplicationError as error:
            assert error.code == "label_deleted"

    with ThreadPoolExecutor(2) as pool:
        assigned = pool.submit(assign)
        deleted = pool.submit(labels.delete, first.id, custom.id, Edit(expected_revision=1))
        assigned.result()
        assert deleted.result().deleted_at
    assert store.task("core", task.id).labels == []


def test_label_http_and_coordinator_tools_are_revision_guarded_and_bounded(tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        identity = client.post("/api/workspaces", json={"name": "Product"}).json()["id"]
        base = f"/api/workspaces/{identity}/labels"
        defaults = client.get(base).json()
        assert {item["id"] for item in defaults} == {
            "feature",
            "bug",
            "maintenance",
            "investigation",
        }
        created = client.post(
            base, json={"name": "Docs", "color": "pink", "guidance": "Documentation"}
        )
        assert created.status_code == 201
        label = created.json()
        assert (
            client.put(
                base + "/" + label["id"], json={"expected_revision": 1, "color": "cyan-light"}
            ).status_code
            == 200
        )
        assert (
            next(item for item in client.get(base).json() if item["id"] == label["id"])["color"]
            == "cyan-light"
        )
        assert (
            client.request(
                "DELETE", base + "/" + label["id"], json={"expected_revision": 1}
            ).status_code
            == 409
        )
        assert client.request(
            "DELETE", base + "/" + label["id"], json={"expected_revision": 2}
        ).json()["deleted_at"]
        store = client.app.state.supervisor.workspace

        async def check():
            grant = await workspace_scope(Supervisor(store), identity, author="coordinator:labels")
            result = await grant.call(
                "create_label", {"label": {"name": "Shared", "guidance": "🦉" * 1000}}
            )
            assert (
                not result.isError
                and result.structuredContent["updated_by"] == "coordinator:labels"
            )
            for index in range(15):
                Labels(store).create(
                    identity, LabelCreate(name=f"Long {index}", guidance="🦉" * 1000)
                )
            page = await grant.call("list_labels", {"limit": 50})
            assert not page.isError and size(page.structuredContent) <= PAGE_BYTES
            assert page.structuredContent["next_cursor"]
            forbidden = await grant.call(
                "create_label", {"workspace_id": "foreign", "label": {"name": "Escaped"}}
            )
            assert forbidden.isError
            grant.revoke()

        asyncio.run(check())


def test_classification_upgrade_keeps_original_revisions_and_rolls_back(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 61)
        )
        store = fixture(tmp_path).workspace
        with store.connection(write=True) as db:
            for table in ("tasks", "task_revisions"):
                db.execute(
                    f"UPDATE {table} SET data=json_set("
                    "json_remove(data,'$.labels'),'$.task_type','bug')"
                )
        with store.connection() as db:
            original = [tuple(row) for row in db.execute("SELECT * FROM task_revisions")]
    before = logical_data(store.directory)
    migration = next(m for m in migrations.MIGRATIONS if m.version == 61)

    def fail(db):
        migration.apply(db)
        raise ValueError("fixture label migration failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(61, fail) if m.version == 61 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    upgraded = Workspace(store.directory)
    assert upgraded.task("harbor", "task-0").labels == ["bug"]
    with upgraded.connection() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM task_revisions")] == original
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    Labels(upgraded).delete("harbor", "bug", Edit(expected_revision=1))
    current = upgraded.task("harbor", "task-0")
    assert current.labels == [] and current.revisions[0].labels == ["bug"]


def test_cli_repeated_labels_clear_and_discover(monkeypatch):
    from typer.testing import CliRunner

    from flowfield.cli import app

    calls = []

    def request(self, method, path, payload=None):
        calls.append((method, path, payload))
        return [] if method == "GET" else {"id": "task", "revision": 2}

    monkeypatch.setattr("flowfield.client.Client.request", request)
    runner = CliRunner()
    for arguments in (
        ["workspace", "labels", "workspace", "--json"],
        [
            "task",
            "create",
            "--project",
            "core",
            "--title",
            "Task",
            "--label",
            "bug",
            "--label",
            "release",
            "--json",
        ],
        [
            "task",
            "edit",
            "COR-1",
            "--project",
            "core",
            "--expected-revision",
            "1",
            "--no-labels",
            "--json",
        ],
        ["task", "list", "--project", "core", "--label", "bug", "--json"],
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.output
    assert calls[0][1] == "workspaces/workspace/labels"
    assert calls[1][2]["labels"] == ["bug", "release"]
    assert calls[2][2]["labels"] == []
    assert "label_id=bug" in calls[3][1]


def test_label_removal_preserves_exact_approved_delivery(tmp_path):
    from result_fixtures import approve, current
    from result_fixtures import fixture as result_fixture

    service, repo, _ = result_fixture(tmp_path)
    store = service.workspace
    task = store.task("harbor", "work")
    store.edit_task("harbor", task.id, TaskEdit(expected_revision=task.revision, labels=["bug"]))
    service.results.process("harbor")
    ready = current(service)
    assert ready.status == "ready"
    approved = approve(service, ready)
    Labels(store).delete("harbor", "bug", Edit(expected_revision=1))
    assert current(service).approved_at == approved.approved_at
    service.results.process("harbor")
    delivered = current(service)
    assert delivered.status == "delivered"
    assert delivered.candidate_commit == ready.candidate_commit
    assert delivered.approved_at == approved.approved_at
    assert store.task("harbor", task.id).status == "done"
    assert (repo / "result.txt").read_text() == "implemented\n"
