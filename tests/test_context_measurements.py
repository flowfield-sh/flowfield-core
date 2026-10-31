"""Numeric attribution is bounded, private, scoped and compatible with published stores."""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ListToolsRequest,
    TextContent,
    Tool,
    ToolAnnotations,
)
from usage_fixtures import accounting

from flowfield import context_measurements, context_schema, storage
from flowfield.agent_tools import ScopedTools
from flowfield.api import create_app
from flowfield.application import Workspace
from flowfield.context_measurements import ContextMeasurements
from flowfield.errors import ApplicationError


def test_boundary_measurements_repeats_bounds_and_no_content(tmp_path, monkeypatch):
    execution, _, (run,) = accounting(tmp_path)
    record = ContextMeasurements(execution.workspace, "harbor", "run", run.id)
    assert record.read()["coverage"] == "unavailable"
    secret = "PRIVATE 🦉 CONTENT"
    text = json.dumps({"instructions": secret}, ensure_ascii=False)
    assert record.prompt(text) == text
    tools = [
        Tool(
            name="read_context",
            description="Read",
            inputSchema={"type": "object"},
            annotations=ToolAnnotations(readOnlyHint=True),
        )
    ]

    async def read(name, arguments):
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "text": secret,
                            "offset": 0,
                            "end_offset": len(secret),
                            "total_chars": len(secret),
                            "next_offset": None,
                        }
                    ),
                )
            ]
        )

    scope = ScopedTools(tools, read)
    scope.measurements = record

    async def check():
        record.tools(tools)
        for arguments in (
            {"query": secret, "section": "description", "mode": "search"},
            {"mode": "search", "section": "description", "query": secret},
        ):
            result = await scope.call("read_context", arguments)
            assert secret in json.loads(result.content[0].text)["text"]

    asyncio.run(check())
    report = record.read(limit=2)
    assert report["repeated_read_requests"] == 1 and report["next_after"]
    assert record.read(after=report["next_after"])["next_after"] is None
    assert report["items"][0]["bytes"] == len(text.encode())
    retrieved = record.read()["items"][-1]
    assert retrieved["source"] == {"section": "description"} and retrieved["mode"] == "search"
    assert secret not in json.dumps(record.read(), ensure_ascii=False)
    monkeypatch.setattr(context_measurements, "MAX_EVENTS", 4)
    record.prompt("more")
    assert record.read()["coverage"] == "partial" and record.read()["dropped_events"] == 1
    with pytest.raises(ApplicationError):
        ContextMeasurements(execution.workspace, "other", "run", run.id).read()
    app = create_app(data_dir=execution.workspace.directory)
    app.state.workspace = execution.workspace
    with TestClient(app, base_url="http://localhost") as client:
        assert (
            client.get(f"/api/workspaces/harbor/usage/run/{run.id}/context").json()[
                "dropped_events"
            ]
            == 1
        )
        assert client.get(f"/api/workspaces/other/usage/run/{run.id}/context").status_code == 404


def test_measurement_failure_does_not_repeat_successful_mutation(tmp_path, monkeypatch):
    execution, _, (run,) = accounting(tmp_path)
    record = ContextMeasurements(execution.workspace, "harbor", "run", run.id)

    def failed(*args):
        raise sqlite3.OperationalError("unavailable")

    monkeypatch.setattr(record, "_record", failed)
    assert record.prompt("still returned unchanged") == "still returned unchanged"
    applied = []

    async def mutate(name, arguments):
        applied.append(arguments)
        return CallToolResult(content=[TextContent(type="text", text="saved")])

    scope = ScopedTools([Tool(name="save", inputSchema={"type": "object"})], mutate)
    scope.measurements = record
    result = asyncio.run(scope.call("save", {"value": 1}))
    assert result.content[0].text == "saved" and applied == [{"value": 1}]


def test_protocol_validation_errors_are_measured_once_without_dispatch(tmp_path):
    execution, _, (run,) = accounting(tmp_path)
    record = ContextMeasurements(execution.workspace, "harbor", "run", run.id)
    applied = []

    async def mutate(name, arguments):
        applied.append(arguments)
        return CallToolResult(content=[TextContent(type="text", text="saved")])

    scope = ScopedTools(
        [
            Tool(
                name="save",
                inputSchema={
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ],
        mutate,
    )
    scope.measurements = record

    async def check():
        call = scope.server.request_handlers[CallToolRequest]
        for arguments in ({"unexpected": "PRIVATE"}, {"value": "wrong"}, {"value": 1}):
            result = await call(
                CallToolRequest(params=CallToolRequestParams(name="save", arguments=arguments))
            )
            assert result.root.isError == (arguments != {"value": 1})
        # Internal schema-cache population did not send tool definitions to a client.
        assert all(item["kind"] != "tool_definitions" for item in record.read()["items"])
        await scope.server.request_handlers[ListToolsRequest](ListToolsRequest())

    asyncio.run(check())
    items = record.read()["items"]
    assert len(items) == 4 and applied == [{"value": 1}]
    assert record.read()["failed_tool_requests"] == 2
    assert [item["is_error"] for item in items[:3]] == [True, True, False]
    assert items[-1]["kind"] == "tool_definitions"
    assert "PRIVATE" not in json.dumps(items)


def test_actual_published_0101_upgrade_and_rollback(tmp_path, monkeypatch):
    from flowfield.application import TaskCreate

    def snapshot(db, names):
        return {
            name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in names
        }

    directory = tmp_path / "state"
    directory.mkdir()
    database = directory / storage.DATABASE
    with sqlite3.connect(database) as db:
        db.executescript((Path(__file__).parent / "fixtures/published-schema-93.sql").read_text())
        before = db.execute("SELECT id,data FROM tasks ORDER BY id").fetchall()
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        all_rows = snapshot(db, names)
        workspace_id = db.execute("SELECT id FROM workspaces").fetchone()[0]
        identity = storage.identity(db)
    with monkeypatch.context() as patch:
        patch.setattr(
            context_schema, "SCHEMA", context_schema.SCHEMA + "INSERT INTO missing VALUES(1);"
        )
        with pytest.raises(sqlite3.OperationalError):
            Workspace(directory)
    with sqlite3.connect(database) as db:
        assert storage.version(db) == 93
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='usage_context'").fetchone()
        assert db.execute("SELECT id,data FROM tasks ORDER BY id").fetchall() == before
        assert snapshot(db, names) == all_rows
    store = Workspace(directory)
    with store.connection() as db:
        assert storage.version(db) == 94 and storage.identity(db) == identity
        assert [tuple(r) for r in db.execute("SELECT id,data FROM tasks ORDER BY id")] == before
        assert db.execute("SELECT count(*) FROM usage_context").fetchone()[0] == 0
        assert snapshot(db, names) == all_rows
    backups = list((directory / "backups").glob("schema-93-*.sqlite3"))
    assert backups
    with sqlite3.connect(backups[0]) as db:
        assert storage.version(db) == 93 and snapshot(db, names) == all_rows
        storage.integrity(db)
    Workspace(directory)
    assert list((directory / "backups").glob("schema-93-*.sqlite3")) == backups
    app = create_app(data_dir=directory)
    app.state.workspace = store
    with TestClient(app, base_url="http://localhost") as client:
        board = client.get(f"/api/workspaces/{workspace_id}/view/board")
        assert board.status_code == 200 and board.json()["tasks"]
    created = store.create_task(workspace_id, TaskCreate(title="After context upgrade"))
    assert store.task(workspace_id, created.id).title == "After context upgrade"
