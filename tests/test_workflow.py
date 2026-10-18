"""Board invariants, revision races, interface parity and state preservation."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt, existing_directory, task_request
from service_fixtures import running_service
from typer.testing import CliRunner

from flowfield.api import create_app
from flowfield.application import ProjectSetup, TaskEdit, TaskPriority, Workspace
from flowfield.cli import app
from flowfield.errors import ApplicationError


def test_board_grouping_order_archive_and_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with TestClient(create_app(data_dir=state), base_url="http://127.0.0.1") as client:
        space = client.post("/api/workspaces", json={"name": "Fixture"}).json()["id"]
        project = client.post(
            f"/api/workspaces/{space}/projects",
            json={"path": existing_directory(str(tmp_path / "harbor"))},
        ).json()
        base = "/api/projects/harbor"
        project = client.put(
            base,
            json={
                "expected_revision": 1,
                "description": "CSV",
                "name": "Harbor",
            },
        ).json()
        config = (tmp_path / "harbor/.flowfield/config.toml").read_bytes()
        # Rename is workspace metadata, not a conflicting change to portable identity.
        assert (
            client.post(
                f"/api/workspaces/{space}/projects", json={"path": str(tmp_path / "harbor")}
            ).json()
            == project
        )
        assert (tmp_path / "harbor/.flowfield/config.toml").read_bytes() == config
        base = f"/api/workspaces/{space}"
        for task_id in ["serializer", "button", "rounding"]:
            payload = {
                "id": task_id,
                "title": task_id,
                "status": "up_next",
                "labels": ["bug" if task_id == "rounding" else "feature"],
            }
            assert client.post(base + "/tasks", json=payload).status_code == 201
        moved = client.post(
            base + "/tasks/rounding/prioritize",
            json={"expected_revision": 1, "status": "up_next", "before_id": "serializer"},
        ).json()
        board = client.get(base + "/board").json()
        assert [t["id"] for t in board["tasks"]] == ["rounding", "serializer", "button"]
        assert moved["revision"] == 2
        assert (
            client.post(
                base + "/tasks/rounding/prioritize",
                json={"expected_revision": 2, "status": "up_next", "before_id": "serializer"},
            ).json()
            == moved
        )
        invalid = client.post(
            base + "/tasks/rounding/prioritize",
            json={"expected_revision": 2, "status": "done", "before_id": "button"},
        )
        assert invalid.status_code == 422
        assert client.get(base + "/tasks/rounding").json() == moved
        archived = client.put(
            base + "/tasks/rounding", json={"expected_revision": 2, "archived": True}
        ).json()
        assert archived["status"] == "up_next" and archived["archived"]
        assert len(client.get(base + "/tasks").json()) == 2
        assert len(client.get(base + "/tasks?include_archived=true").json()) == 3
        assert (
            client.post(
                base + "/tasks/rounding/prioritize",
                json={"expected_revision": 3, "status": "up_next"},
            ).status_code
            == 409
        )
        restored = client.put(
            base + "/tasks/rounding", json={"expected_revision": 3, "archived": False}
        ).json()
        assert restored["revision"] == 4 and restored["status"] == "up_next"
        assert [t["id"] for t in client.get(base + "/tasks").json()] == [
            "serializer",
            "button",
            "rounding",
        ]
        final = client.put(
            base + "/tasks/rounding",
            json={"expected_revision": 4, "title": "Round correctly", "author": "agent"},
        ).json()
        assert final["updated_by"] == "agent"
        assert final["revisions"][2]["archived"]
        assert "approved_revision" not in final
        assert (
            client.post(base + "/tasks/rounding/approval", json={"revision": 5}).status_code == 404
        )
        board = client.get(base + "/board").json()
    with TestClient(create_app(data_dir=state), base_url="http://127.0.0.1") as client:
        assert client.get(base + "/board").json() == board
        assert client.get(base + "/tasks/rounding").json() == final
        assert not (state / "access-token").exists()


def test_validation_and_scoped_atomic_edits(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path / "state"), base_url="http://localhost") as client:
        spaces = {}
        for identity in ["one", "two"]:
            space = client.post("/api/workspaces", json={"name": identity}).json()["id"]
            spaces[identity] = space
            client.post(
                f"/api/workspaces/{space}/projects",
                json={"id": identity, "path": existing_directory(str(tmp_path / identity))},
            )
        base = f"/api/workspaces/{spaces['one']}/tasks"
        foreign = client.post(
            f"/api/workspaces/{spaces['two']}/tasks", json={"id": "foreign", "title": "Other"}
        ).json()
        assert (
            client.post(base, json={"title": "Bad", "dependencies": [foreign["id"]]}).status_code
            == 400
        )
        assert client.get(base).json() == []
        task = client.post(
            base,
            json={
                "title": "New task",
                "body": "Original",
            },
        ).json()
        path = base + "/" + task["id"]
        assert (
            client.put(
                path,
                json={"expected_revision": 1, "dependencies": [foreign["id"]], "title": "Lost"},
            ).status_code
            == 400
        )
        assert client.get(path).json() == task
        for invalid in [
            {"title": "  "},
            {"body": None},
            {"archived": None},
            {"task_type": "nonsense"},
            {"status": "done"},
            {"dependencies": None},
        ]:
            assert client.put(path, json={"expected_revision": 1, **invalid}).status_code == 422
        updated = client.put(path, json={"expected_revision": 1, "body": "Test passes"}).json()
        assert updated["body"] == "Test passes" and updated["revision"] == 2
        assert (
            client.put(path, json={"expected_revision": 2, "body": "Test passes"}).json() == updated
        )
        assert client.put(path, json={"expected_revision": 1, "body": "Lost"}).status_code == 409
        assert (
            client.post(
                path + "/progress", json={"expected_revision": 1, "status": "done"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                path + "/prioritize",
                json={"expected_revision": 2, "status": "backlog", "before_id": task["id"]},
            ).status_code
            == 400
        )
        assert client.get(f"/api/workspaces/{spaces['two']}/tasks/" + task["id"]).status_code == 404
        project = client.put(
            "/api/projects/one", json={"expected_revision": 1, "description": "Next"}
        ).json()
        assert (
            client.put(
                "/api/projects/one", json={"expected_revision": 1, "description": "Stale"}
            ).status_code
            == 409
        )
        assert client.get("/api/projects/one").json() == project
        for headers in [
            {"Origin": "https://evil.example"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            assert (
                client.put(
                    path, json={"expected_revision": 2, "body": "Lost"}, headers=headers
                ).status_code
                == 403
            )


def test_concurrent_move_and_edit_have_one_winner(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "state")
    adopt(workspace, ProjectSetup(path=str(tmp_path / "harbor")))
    workspace.create_task("harbor", task_request(id="export", title="Export", body="First"))

    def mutate(kind: str) -> str:
        try:
            if kind == "edit":
                workspace.edit_task(
                    "harbor", "export", TaskEdit(expected_revision=1, body="Second")
                )
            else:
                workspace.prioritize_task(
                    "harbor", "export", TaskPriority(expected_revision=1, status="up_next")
                )
            return "saved"
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, ["edit", "move"])) == ["revision_conflict", "saved"]
    assert len(workspace.task("harbor", "export").revisions) == 2


def test_cli_board_and_readable_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    with running_service(tmp_path / "state") as base:
        prefix = ["--port", base.rsplit(":", 1)[1]]

        def command(args: list[str], success: bool = True) -> dict | list:
            if args[0] in {"task", "status"}:
                args = [*args, "--workspace", space["id"]]
            result = runner.invoke(app, prefix + args + ["--json"])
            assert result.exit_code == (0 if success else 1), result.output
            assert (result.stderr if success else result.stdout) == ""
            return json.loads(result.stdout if success else result.stderr)

        space = command(["workspace", "create", "Product"])
        project = command(
            [
                "project",
                "init",
                existing_directory(str(tmp_path / "harbor")),
                "--workspace",
                space["id"],
            ]
        )
        monkeypatch.chdir(tmp_path / "harbor")
        assert command(["project", "init", "--workspace", space["id"]]) == project
        command(["project", "edit", "--description", "CSV"])
        assert command(["project", "show"])["description"] == "CSV"
        task = command(
            [
                "task",
                "create",
                "--title",
                "Serializer",
                "--body",
                "Quotes commas",
            ]
        )
        identity = task["id"]
        command(["task", "prioritize", identity, "up-next"])
        command(["task", "edit", identity, "--body", "Keep filters"])
        shown = command(["task", "show", identity])
        assert shown["revision"] == 3
        error = command(
            ["task", "edit", identity, "--expected-revision", "1", "--title", "Stale"], False
        )
        assert error["error"]["code"] == "revision_conflict"
        command(["task", "archive", identity])
        assert command(["task", "list"]) == {"items": [], "next_cursor": None}
        command(["task", "restore", identity])
        assert command(["status"])["columns"][1]["tasks"][0]["id"] == identity
        human = runner.invoke(app, prefix + ["status", "--workspace", space["id"]])
        assert human.exit_code == 0 and "Up next (1)" in human.stdout and "Product" in human.stdout
        human = runner.invoke(app, prefix + ["task", "list", "--workspace", space["id"]])
        assert (
            human.exit_code == 0
            and "Serializer" in human.stdout
            and not human.stdout.startswith("[")
        )
        missing = command(
            ["task", "edit", identity, "--body-file", str(tmp_path / "missing")], False
        )
        assert missing["error"]["code"] == "file_error"
        for retired in [
            ["init"],
            ["connect", "codex"],
            ["task", "move", identity, "done"],
            ["task", "progress", identity, "done"],
            ["task", "approve", identity],
            ["task", "revise", identity],
            ["project", "register"],
        ]:
            assert runner.invoke(app, retired).exit_code == 2
    result = runner.invoke(app, prefix + ["project", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "service_unavailable"


def test_priority_cannot_change_progress_and_times_survive_edits(tmp_path):
    from execution_fixtures import BASE, fixture

    from flowfield.browser import BrowserReads

    execution = fixture(tmp_path, count=2)
    store = execution.workspace
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)
    task = store.task("harbor", run.task_id)
    for revision in (1, task.revision):
        with pytest.raises(ApplicationError):
            store.prioritize_task(
                "harbor", task.id, TaskPriority(expected_revision=revision, status="backlog")
            )
    with pytest.raises(ApplicationError):
        store.edit_task("harbor", task.id, TaskEdit(expected_revision=task.revision, archived=True))
    before = BrowserReads(store).task("harbor", task.id).status_changed_at
    renamed = store.edit_task(
        "harbor", task.id, TaskEdit(expected_revision=task.revision, title="Clearer title")
    )
    assert renamed.status == "in_progress"
    assert BrowserReads(store).task("harbor", task.id).status_changed_at == before
