"""Scripted native app-server peer; no model, user config or credential access."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONFIG = [
    {"currentValue": "test-model"},
    {"id": "reasoning_effort", "currentValue": "low"},
    {"currentValue": "read-only"},
]
if "fast" in sys.argv:
    CONFIG.append({"id": "fast-mode", "currentValue": "off"})
active = False
current = {}


def send(message):
    print(json.dumps(message), flush=True)


def reply(request, result):
    global active
    if request.get("prompting"):
        active = False
        send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "test-session",
                    "turn": {
                        "id": "turn-1",
                        "status": "interrupted"
                        if result.get("stopReason") == "cancelled"
                        else "completed",
                    },
                },
            }
        )
    else:
        send({"id": request["id"], "result": result})


def update(update_type, **data):
    common = {"threadId": "test-session", "turnId": "turn-1"}
    if update_type == "agent_message_chunk":
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {**common, "itemId": "message", "delta": data["content"]["text"]},
            }
        )
    elif update_type == "tool_call":
        item = {
            "id": data["toolCallId"],
            "type": "fileChange",
            "status": data["status"],
            "changes": [
                {
                    "path": i.get("path", ""),
                    "diff": "Before: " + i.get("oldText", "") + "\nAfter: " + i.get("newText", ""),
                }
                for i in data.get("content", [])
                if i.get("type") == "diff"
            ],
        }
        send(
            {
                "method": "item/completed" if data["status"] == "completed" else "item/started",
                "params": {**common, "item": item},
            }
        )
    elif update_type == "usage_update":
        send(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    **common,
                    "tokenUsage": {
                        "last": {"totalTokens": data["used"]},
                        "modelContextWindow": data["size"],
                    },
                },
            }
        )
    elif update_type == "agent_thought_chunk":
        send(
            {
                "method": "item/reasoning/textDelta",
                "params": {**common, "delta": data.get("content")},
            }
        )


async def main():
    global active, current
    stopped = asyncio.Event()
    pending = {}
    tasks = set()
    goal_active = "native-background" in sys.argv
    terminal_active = goal_active
    tool_pid = int(os.environ.get("FLOWFIELD_TEST_TOOL_PID", "0"))
    servers = []
    resumed = False
    loaded = False

    def completed(task):
        tasks.discard(task)
        if not task.cancelled() and task.exception():
            global active
            active = False
            print(type(task.exception()).__name__, file=sys.stderr, flush=True)
            send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "test-session",
                        "turn": {"id": "turn-1", "status": "failed"},
                    },
                }
            )

    async def prompt(request):
        if expected := os.environ.get("FLOWFIELD_TEST_FAST"):
            assert next(c for c in CONFIG if c.get("id") == "fast-mode")["currentValue"] == expected
        text = request["params"]["prompt"][0]["text"]
        if text.startswith("/"):
            assert text in {"/status", "/compact", "/skills", "/mcp"}
            update(
                "agent_message_chunk", content={"type": "text", "text": f"Native command: {text}"}
            )
            reply(request, {"stopReason": "end_turn"})
            return
        if "expect-attachments" in sys.argv:
            blocks = request["params"]["prompt"]
            assert blocks[1] == {"type": "text", "text": "Human attachment: notes.txt"}
            assert blocks[2] == {"type": "text", "text": "Preserve the public API."}
            assert blocks[3] == {"type": "text", "text": "Human attachment: screen.png"}
            assert blocks[4]["type"] == "image" and blocks[4]["url"].startswith(
                "data:image/png;base64,"
            )
            assert "iVBOR" in blocks[4]["url"]
        control = json.loads(request["params"]["prompt"][0]["text"])
        if "coordinator" in sys.argv:
            assert control["flowfield_connection"] == servers[0]["name"]
            assert control["flowfield_connection"] in control["instructions"]
            assert CONFIG[2]["currentValue"] == os.environ.get("FLOWFIELD_TEST_MODE", "read-only")
            if "continuity" in control["human_message"]:
                assert resumed and "recent_conversation" not in control
            control = {
                "mode": os.environ.get("FLOWFIELD_TEST_SCENARIO", "normal"),
                "calls": [
                    {
                        "name": "create_task",
                        "arguments": {
                            "task": {
                                "stages": [
                                    {
                                        "id": "report",
                                        "title": "Report",
                                        "outcome": "Report the agreed findings",
                                    }
                                ],
                                "id": "chat-task",
                                "title": "Captured from Coordinator Chat",
                                "body": "An agreed outcome",
                                "author": "human",
                            }
                        },
                    }
                ],
            }
        elif "managed" in sys.argv:
            assert control["flowfield_connection"] == servers[0]["name"]
            assert control["flowfield_connection"] in control["instructions"]
            assert "run_command" not in control["instructions"]
            assert "private Python runtime" not in control["instructions"]
            scenario = os.environ.get("FLOWFIELD_TEST_SCENARIO", "normal")
            if scenario == "monorepo":
                if control["task"].endswith(": Task 0"):
                    Path("services/api/billing.py").write_text("VALUE = 2\n")
                else:
                    assert control["task"].endswith(": Task 1")
                    Path("apps/web/price.mjs").write_text("export const value = 2;\n")
            else:
                Path("result.txt").write_text(os.environ["FLOWFIELD_RUN_ID"])
            control = {
                "mode": scenario,
                "calls": [
                    {
                        "name": "submit_result",
                        "arguments": {
                            "outcome": "complete",
                            "summary": "Native result",
                            "checks": "Deterministic native-tool substitute",
                        },
                    },
                ],
            }
            if scenario == "question":
                control["calls"] = [
                    {
                        "name": "ask_question",
                        "arguments": {
                            "question": "Which behavior?",
                            "context": "Two options remain.",
                            "recommendation": "Use the first option.",
                        },
                    }
                ]
        mode = control.get("mode", "normal")
        if mode == "disconnect":
            os._exit(2)
        if mode == "oversized":
            print("x" * 300000, flush=True)
            return
        if mode == "malformed":
            print("[]", flush=True)
            return
        if mode == "stderr":
            sys.stderr.write("private diagnostic\n" * 20000)
            sys.stderr.flush()
        if mode == "ignore_cancel":
            await asyncio.Event().wait()
        if mode in {"permission", "permission_disconnect"}:
            if "managed" in sys.argv:
                update(
                    "tool_call",
                    toolCallId="tool-1",
                    title="Edit files",
                    kind="edit",
                    status="pending",
                    content=[
                        {
                            "type": "diff",
                            "path": "/project/code.txt",
                            "oldText": "before",
                            "newText": "after",
                        }
                    ],
                )
            future = asyncio.get_running_loop().create_future()
            pending["permission"] = future
            options = [
                {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "deny", "name": "Reject once", "kind": "reject_once"},
            ]
            if "long-permission" in sys.argv:
                options.insert(
                    1,
                    {
                        "optionId": "future",
                        "name": "Yes, and don't ask again for commands that start with `node -e '"
                        + 'fetch("http://127.0.0.1:8902/pocket-list.js");' * 8
                        + 'console.log("complete-prefix")\'`',
                        "kind": "allow_always",
                    },
                )
            send(
                {
                    "id": "permission",
                    "method": "item/fileChange/requestApproval",
                    "params": {
                        "threadId": "test-session",
                        "turnId": "turn-1",
                        "itemId": "tool-1",
                        "reason": "command: inspect project",
                        "toolCall": {
                            "toolCallId": "tool-1",
                            "title": "Check",
                            "content": [
                                {
                                    "type": "content",
                                    "content": {"type": "text", "text": "command: inspect project"},
                                }
                            ],
                        },
                        "options": options,
                    },
                }
            )
            if mode == "permission_disconnect":
                await asyncio.sleep(0.15)
                os._exit(2)
            value = await future
            update("agent_message_chunk", content={"type": "text", "text": json.dumps(value)})
        if mode == "wait":
            update("agent_message_chunk", content={"type": "text", "text": "started"})
            await stopped.wait()
        for server in servers if control.get("calls") else []:
            headers = {item["name"]: item["value"] for item in server["headers"]}
            async with httpx.AsyncClient(headers=headers) as client:
                async with streamable_http_client(server["url"], http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as mcp:
                        await mcp.initialize()
                        catalog = await mcp.list_tools()
                        if "managed" in sys.argv:
                            assert "run_command" not in {tool.name for tool in catalog.tools}
                        update(
                            "agent_message_chunk",
                            content={
                                "type": "text",
                                "text": json.dumps(
                                    {"tools": [tool.model_dump() for tool in catalog.tools]}
                                ),
                            },
                        )
                        for call in control["calls"]:
                            result = await mcp.call_tool(call["name"], call.get("arguments", {}))
                            update(
                                "agent_message_chunk",
                                content={
                                    "type": "text",
                                    "text": json.dumps(
                                        {
                                            "call": call["name"],
                                            "result": result.model_dump(mode="json"),
                                        }
                                    ),
                                },
                            )
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "wrong-session",
                    "itemId": "message",
                    "delta": "WRONG SESSION",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "WRONG SESSION"},
                    },
                },
            }
        )
        update("agent_thought_chunk", content={"type": "text", "text": "PRIVATE REASONING"})
        update(
            "agent_thought_chunk",
            content={"type": "text", "text": None, "unexpected": "PRIVATE MALFORMED REASONING"},
        )
        update(
            "tool_call",
            toolCallId="tool-1",
            title="Check",
            kind="read",
            status="completed",
            rawInput={"secret": "PRIVATE TOOL INPUT"},
        )
        update("usage_update", used=100, size=1000)
        update("agent_message_chunk", content={"type": "text", "text": "finished"})
        reply(request, {"stopReason": "cancelled" if stopped.is_set() else "end_turn"})

    while line := await asyncio.to_thread(sys.stdin.readline):
        request = json.loads(line)
        method, params = request.get("method"), request.get("params", {})
        if method == "initialize":
            if "slow-start" in sys.argv:
                await asyncio.Event().wait()
            reply(request, {"userAgent": "scripted-native"})
        elif method == "model/list":
            reply(
                request,
                {
                    "data": [
                        {
                            "id": "test-model",
                            "model": "test-model",
                            "displayName": "Test model",
                            "hidden": False,
                            "supportedReasoningEfforts": []
                            if "no-effort" in sys.argv
                            else [{"reasoningEffort": "low"}],
                            "additionalSpeedTiers": ["fast"] if "fast" in sys.argv else [],
                            "inputModalities": ["text"]
                            if "no-images" in sys.argv
                            else ["text", "image"],
                        }
                    ],
                    "nextCursor": None,
                },
            )
        elif method == "mcpServerStatus/list":
            reply(
                request,
                {
                    "data": [{"name": "flowfield", "connectionStatus": "connected"}],
                    "nextCursor": None,
                },
            )
        elif method == "skills/list":
            reply(request, {"data": [{"cwd": str(Path.cwd()), "skills": [], "errors": []}]})
        elif method == "config/read":
            reply(request, {"config": {"mcp_servers": {"ambient": {"enabled": True}}}})
        elif method in {"thread/start", "thread/resume"}:
            if method == "thread/resume" and (
                "no-resume" in sys.argv or "resume-missing" in sys.argv
            ):
                send({"id": request["id"], "error": {"code": -32000}})
                continue
            loaded = True
            resumed = method == "thread/resume"
            CONFIG[2]["currentValue"] = (
                "read-only"
                if params["sandbox"] == "read-only"
                else "agent-full-access"
                if params["sandbox"] == "danger-full-access"
                else "agent"
                if params["approvalsReviewer"] == "auto_review"
                else "workspace-write"
            )
            if "fast" in sys.argv:
                next(c for c in CONFIG if c.get("id") == "fast-mode")["currentValue"] = (
                    "on" if params.get("serviceTier") == "fast" else "off"
                )
            servers = [
                {
                    "name": name,
                    "url": config["url"],
                    "headers": [
                        {"name": k, "value": v} for k, v in config.get("http_headers", {}).items()
                    ],
                }
                for name, config in params.get("config", {}).get("mcp_servers", {}).items()
                if config.get("enabled")
            ]
            current = {
                "thread": {"id": "test-session"},
                "model": params["model"],
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "sandbox": {
                    "type": {
                        "read-only": "readOnly",
                        "workspace-write": "workspaceWrite",
                        "danger-full-access": "dangerFullAccess",
                    }[params["sandbox"]]
                },
                "reasoningEffort": params["config"].get("model_reasoning_effort"),
                "serviceTier": "priority" if params.get("serviceTier") == "fast" else "default",
            }
            reply(request, current)
        elif method == "thread/loaded/list":
            if "cleanup-uncertain" in sys.argv:
                send({"id": request["id"], "error": {"code": -32000}})
            else:
                reply(request, {"data": ["test-session"] if loaded else [], "nextCursor": None})
        elif method == "thread/read":
            reply(
                request,
                {
                    "thread": {
                        "id": params["threadId"],
                        "ephemeral": False,
                        "status": {"type": "active" if active else "idle"},
                    }
                },
            )
        elif method == "thread/goal/get":
            reply(
                request,
                {
                    "goal": {"status": "active" if goal_active else "paused"}
                    if "native-background" in sys.argv
                    else None
                },
            )
        elif method == "thread/goal/set":
            if "goal-refused" not in sys.argv:
                goal_active = False
            reply(request, {})
        elif method == "thread/backgroundTerminals/list":
            reply(
                request,
                {
                    "data": [{"processId": "owned-native-terminal"}] if terminal_active else [],
                    "nextCursor": None,
                },
            )
        elif method == "thread/backgroundTerminals/terminate":
            assert params["processId"] == "owned-native-terminal"
            if "terminal-refused" in sys.argv:
                reply(request, {"terminated": False})
            else:
                os.kill(tool_pid, signal.SIGTERM)
                terminal_active = False
                reply(request, {"terminated": True})
        elif method in {"turn/start", "thread/compact/start"}:
            if method == "thread/compact/start" and "no-compact" in sys.argv:
                send({"id": request["id"], "error": {"code": -32601}})
                continue
            active = True
            stopped.clear()
            send(
                {
                    "method": "turn/started",
                    "params": {"threadId": "test-session", "turn": {"id": "turn-1"}},
                }
            )
            reply(request, {"turn": {"id": "turn-1", "status": "inProgress"}})
            request["prompting"] = True
            request["params"]["prompt"] = params.get(
                "input", [{"type": "text", "text": "/compact"}]
            )
            task = asyncio.create_task(prompt(request))
            tasks.add(task)
            task.add_done_callback(completed)
        elif method == "turn/interrupt":
            if os.environ.get("FLOWFIELD_TEST_SCENARIO") != "ignore_cancel":
                stopped.set()
                for future in pending.values():
                    if not future.done():
                        future.set_result({"outcome": {"outcome": "cancelled"}})
            reply(request, {})
        elif "id" in request and not method:
            future = pending.get(request["id"])
            if future and not future.done():
                decision = request.get("result", {}).get("decision")
                future.set_result(
                    {
                        "outcome": {
                            "outcome": "selected",
                            "optionId": "allow" if decision == "accept" else "deny",
                        }
                    }
                )
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


asyncio.run(main())
