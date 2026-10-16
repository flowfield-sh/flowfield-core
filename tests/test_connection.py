"""Real MCP and SSE transports against the service, without a model or harness."""

import json
from pathlib import Path

import anyio
import httpx
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from project_fixtures import existing_directory, task_request
from service_fixtures import running_service

from flowfield.api import create_app


def test_mcp_board_parity_and_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"

    async def exercise(base: str, first: bool) -> None:
        with anyio.fail_after(20):
            async with (
                streamable_http_client(base + "/mcp/") as (read, write, _),
                ClientSession(read, write) as session,
                httpx.AsyncClient(base_url=base, trust_env=False) as api,
            ):
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "Flowfield"
                assert "explicit project_id" in initialized.instructions
                from flowfield.guidance import template

                assert initialized.instructions == template("mcp-instructions.md").strip()
                assert len(initialized.instructions) < 3000
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert all(
                    initialized.instructions not in (tool.description or "")
                    for tool in tools.values()
                )
                assert all(tool.description and tool.inputSchema for tool in tools.values())
                config = tools["configure_integration"].inputSchema["$defs"]["IntegrationConfig"][
                    "properties"
                ]
                assert "environment" not in config and "runtime" in config
                assert {
                    "edit_task",
                    "get_task",
                    "get_board",
                    "get_task_input",
                    "reply_to_task",
                    "cancel_task_reply",
                } <= tools.keys()
                assert (
                    not {
                        "approve_task",
                        "revise_task",
                        "register_project",
                        "create_project",
                        "publish_task",
                        "record_progress",
                        "review_run",
                        "check_run_integration",
                        "prepare_integration",
                        "apply_integration",
                    }
                    & tools.keys()
                )
                assert {"get_project_guidance", "update_project_guidance"} <= tools.keys()
                for name, tool in tools.items():
                    if name not in {
                        "list_projects",
                        "list_worker_models",
                        "list_workspaces",
                        "create_workspace",
                    }:
                        assert {"project_id", "workspace_id"}.intersection(
                            tool.inputSchema["required"]
                        )
                assert tools["get_board"].annotations.readOnlyHint
                assert not tools["prioritize_task"].annotations.readOnlyHint

                async def call(name: str, args: dict | None = None) -> dict:
                    result = await session.call_tool(name, args or {})
                    assert not result.isError, result
                    assert json.loads(result.content[0].text) == result.structuredContent
                    return result.structuredContent

                identity = {"project_id": "harbor", "task_id": "export"}
                if first:
                    assert await call("list_projects") == {"items": [], "next_cursor": None}
                    assert await call("list_workspaces") == {"items": [], "next_cursor": None}
                    space = await call("create_workspace", {"request": {"name": "Product"}})
                    created = await call(
                        "adopt_workspace_project",
                        {
                            "workspace_id": space["id"],
                            "project": {
                                "id": "harbor",
                                "path": existing_directory(str(tmp_path / "harbor")),
                            },
                        },
                    )
                    assert (
                        await call(
                            "adopt_workspace_project",
                            {
                                "workspace_id": space["id"],
                                "project": {"id": "harbor", "path": str(tmp_path / "harbor")},
                            },
                        )
                        == created
                    )
                    setup = await call("get_integration_settings", {"project_id": "harbor"})
                    preview = await call("get_project_guidance", {"project_id": "harbor"})
                    compact = await call(
                        "get_project_guidance", {"project_id": "harbor", "preview": False}
                    )
                    assert compact.pop("preview_omitted") is True
                    assert compact.pop("preview_chars") == {
                        key: len(preview[key]) for key in ("section", "skill")
                    }
                    assert compact == {
                        key: value
                        for key, value in preview.items()
                        if key not in ("section", "skill")
                    }
                    assert len(json.dumps(compact)) < len(json.dumps(preview)) / 3
                    assert preview == await call(
                        "get_project_guidance", {"project_id": "harbor", "preview": True}
                    )
                    assert setup["environment_info"]["runtime"] == "local"
                    assert "Service host" in setup["environment_info"]["description"]
                    await call(
                        "edit_project",
                        {
                            "project_id": "harbor",
                            "changes": {
                                "expected_revision": 1,
                                "description": "CSV",
                            },
                        },
                    )
                    await call(
                        "create_milestone",
                        {
                            "workspace_id": space["id"],
                            "milestone": {"id": "csv", "title": "CSV export"},
                        },
                    )
                    await call(
                        "create_task",
                        {
                            "project_id": "harbor",
                            "task": {
                                "stages": task_request(title="Fixture").model_dump()["stages"],
                                "id": "export",
                                "title": "Export",
                                "body": "Keep filters",
                                "milestone_id": "csv",
                            },
                        },
                    )
                    task = await call("get_task", identity)
                    assert task["updated_by"] == "agent" and task["status"] == "backlog"
                    assert (
                        task == (await api.get("/api/context/projects/harbor/tasks/export")).json()
                    )
                    assert (await session.call_tool("get_task", {"task_id": "export"})).isError
                    assert (
                        await session.call_tool("get_task", {**identity, "project_id": "missing"})
                    ).isError
                    await call(
                        "prioritize_task",
                        {**identity, "priority": {"expected_revision": 1, "status": "up_next"}},
                    )
                    await api.put(
                        "/api/projects/harbor/tasks/export",
                        json={"expected_revision": 2, "body": "Preserve row order."},
                    )
                    for name, parameter, change in [
                        ("edit_task", "changes", {"title": "Stale"}),
                    ]:
                        conflict = await session.call_tool(
                            name, {**identity, parameter: {"expected_revision": 1, **change}}
                        )
                        assert (
                            conflict.isError
                            and conflict.structuredContent["error"]["code"] == "revision_conflict"
                        )
                    await call(
                        "edit_task",
                        {
                            **identity,
                            "changes": {
                                "expected_revision": 3,
                                "body": "Quotes commas.",
                                "milestone_id": None,
                            },
                        },
                    )
                task = await call("get_task", identity)
                assert task["revision"] == 4 and task["status"] == "up_next"
                if first:
                    await call(
                        "update_task_stages",
                        {
                            **identity,
                            "request": {
                                "expected_revision": 1,
                                "agreement_revision": task["agreement_revision"],
                                "stages": [
                                    {
                                        "id": "implement",
                                        "title": "Implement",
                                        "outcome": "Deliver CSV export",
                                    }
                                ],
                                "reason": "Plan the agreed outcome",
                            },
                        },
                    )
                stage = await call("get_task_stages", identity)
                assert stage == (await api.get("/api/projects/harbor/tasks/export/stages")).json()
                assert stage["revision"] == 2
                conversation = await call("get_task_conversation", identity)
                assert (
                    conversation
                    == (await api.get("/api/projects/harbor/tasks/export/conversation")).json()
                )
                source = await call("get_conversation_source", {**identity, "item_id": "plan:2"})
                assert json.loads(source["text"])["reason"] == "Plan the agreed outcome"
                assert (
                    await api.get("/api/projects/harbor/tasks/export/input-eligibility")
                ).json() == await call("get_task_input", identity)
                assert "revisions" not in task and task["milestone_id"] is None
                revisions = await call("list_task_revisions", identity)
                assert len(revisions["items"]) == 4
                board = await call("get_board", {"project_id": "harbor"})
                other = (await api.get("/api/context/projects/harbor/board")).json()
                assert board.pop("observed_at") <= other.pop("observed_at")
                assert board["recent_changes"].pop("window_start") <= other["recent_changes"].pop(
                    "window_start"
                )
                assert board == other
                assert board["project"]["url"] == base + "/projects/harbor"
                assert board["links"]["inbox"] == base + "/projects/harbor/inbox"
                assert board["columns"][1]["tasks"][0]["url"] == (
                    base + "/projects/harbor/tasks/HAR-1"
                )
                assert board["columns"][1]["tasks"][0]["id"] == task["id"]
                assert board["project"]["description"] == "CSV"
                listed = await call("list_tasks", {"project_id": "harbor"})
                assert listed["items"][0]["id"] == task["id"] and listed["next_cursor"] is None
                assert "body" not in listed["items"][0]

    for first in [True, False]:
        with running_service(state) as base:
            anyio.run(exercise, base, first)


def test_sse_mutations_and_reconnection(tmp_path: Path) -> None:
    async def exercise(base: str) -> None:
        with anyio.fail_after(15):
            async with httpx.AsyncClient(base_url=base, trust_env=False) as client:
                space = (await client.post("/api/workspaces", json={"name": "Fixture"})).json()[
                    "id"
                ]
                async with client.stream("GET", "/api/events") as response:
                    assert response.headers["content-type"].startswith("text/event-stream")
                    lines = response.aiter_lines()

                    async def change() -> None:
                        async for line in lines:
                            if line == "event: change":
                                return
                        raise AssertionError("Stream closed before change")

                    await change()  # Initial resync.
                    created = await client.post(
                        f"/api/workspaces/{space}/projects",
                        json={
                            "id": "harbor",
                            "path": existing_directory(str(tmp_path / "harbor")),
                        },
                    )
                    assert created.status_code == 201
                    await change()
                    async with (
                        streamable_http_client(base + "/mcp/") as (read, write, _),
                        ClientSession(read, write) as mcp,
                    ):
                        await mcp.initialize()
                        result = await mcp.call_tool(
                            "create_task",
                            {
                                "project_id": "harbor",
                                "task": {
                                    "stages": task_request(title="Fixture").model_dump()["stages"],
                                    "id": "export",
                                    "title": "Export",
                                    "body": "Plan",
                                },
                            },
                        )
                        assert not result.isError
                    await change()  # MCP commits use the same notifications as HTTP.
                await client.post(
                    "/api/projects/harbor/tasks/export/progress",
                    json={"expected_revision": 1, "status": "up_next"},
                )
                async with client.stream(
                    "GET", "/api/events", headers={"Last-Event-ID": "obsolete"}
                ) as response:
                    async for line in response.aiter_lines():
                        if line == "event: change":
                            break
                    else:
                        raise AssertionError("Reconnect must resync")
                assert (await client.get("/api/projects/harbor/tasks/export")).json()[
                    "status"
                ] == "up_next"

    with running_service(tmp_path / "state") as base:
        anyio.run(exercise, base)


def test_connection_endpoints_enforce_local_origin(tmp_path: Path) -> None:
    with TestClient(create_app(data_dir=tmp_path), base_url="http://127.0.0.1") as client:
        for path in ["/mcp/", "/api/events"]:
            for headers in [
                {"Origin": "https://evil.example"},
                {"Origin": "null"},
                {"Sec-Fetch-Site": "cross-site"},
            ]:
                assert client.get(path, headers=headers).status_code == 403
            assert client.get(path, headers={"Host": "evil.example"}).status_code == 400
