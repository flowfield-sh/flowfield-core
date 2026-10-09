"""Deterministic ACP peer. Commands in its prompt are test data, never model work."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONFIG = [
    {
        "id": "model",
        "name": "Model",
        "category": "model",
        "type": "select",
        "currentValue": "first",
        "options": [{"value": "first", "name": "First"}, {"value": "second", "name": "Second"}],
    }
]
if "managed" in sys.argv:
    CONFIG[0]["options"] = [{"value": "test-model", "name": "Test model"}]
    CONFIG[0]["currentValue"] = "test-model"
    CONFIG.extend(
        [
            {
                "id": "reasoning_effort",
                "name": "Effort",
                "type": "select",
                "currentValue": "low",
                "options": [{"value": "low", "name": "Low"}],
            },
            {
                "id": "mode",
                "name": "Mode",
                "type": "select",
                "currentValue": "read-only",
                "options": [
                    {"value": v, "name": v}
                    for v in ("workspace-write", "read-only", "agent", "agent-full-access")
                ],
            },
        ]
    )


if "fast" in sys.argv:
    CONFIG.append(
        {
            "id": "fast-mode",
            "name": "Fast mode",
            "type": "select",
            "currentValue": "on",
            "description": "Faster responses, increased usage",
            "options": [{"value": "on", "name": "On"}, {"value": "off", "name": "Off"}],
        }
    )


def send(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def reply(request, result):
    send({"id": request["id"], "result": result})


def update(update_type, **data):
    send(
        {
            "method": "session/update",
            "params": {
                "sessionId": "test-session",
                "update": {"sessionUpdate": update_type, **data},
            },
        }
    )


async def main():
    stopped = asyncio.Event()
    pending = {}
    tasks = set()
    servers = []
    session_closed = False
    resumed = False

    async def prompt(request):
        if expected := os.environ.get("FLOWFIELD_TEST_FAST"):
            assert next(c for c in CONFIG if c["id"] == "fast-mode")["currentValue"] == expected
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
            assert blocks[4]["type"] == "image" and blocks[4]["mimeType"] == "image/png"
            assert blocks[4]["data"].startswith("iVBOR")
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
                            "summary": "ACP result",
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
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "test-session",
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
                "method": "session/update",
                "params": {
                    "sessionId": "wrong-session",
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
        method = request.get("method")
        if method == "initialize":
            if "slow-start" in sys.argv:
                await asyncio.Event().wait()
            reply(
                request,
                {
                    "protocolVersion": 99 if "bad-version" in sys.argv else 1,
                    "agentCapabilities": {
                        "_meta": (
                            {
                                "flowfield.cleanup": {
                                    "version": 1,
                                    "method": "_flowfield/quiesce",
                                    "scope": "native-turns-and-terminals",
                                }
                            }
                            if "cleanup" in sys.argv
                            else {}
                        ),
                        "loadSession": "no-load" not in sys.argv,
                        "promptCapabilities": {"image": "no-images" not in sys.argv},
                        "mcpCapabilities": {"http": "no-http" not in sys.argv},
                        "sessionCapabilities": {
                            **({"close": {}} if "close-session" in sys.argv else {}),
                            **({"resume": {}} if "no-resume" not in sys.argv else {}),
                        },
                    },
                },
            )
        elif method in {"session/new", "session/load", "session/resume"}:
            if expected := os.environ.get("FLOWFIELD_TEST_META"):
                assert request["params"].get("_meta") == json.loads(expected)
            resumed = method == "session/resume"
            servers = request["params"]["mcpServers"]
            if request["params"].get("sessionId") == "missing" or (
                resumed and "resume-missing" in sys.argv
            ):
                send({"id": request["id"], "error": {"code": -32000, "message": "Session missing"}})
            else:
                if "early-commands" not in sys.argv:
                    reply(request, {"sessionId": "test-session", "configOptions": CONFIG})
                if "no-commands" not in sys.argv:
                    update(
                        "available_commands_update",
                        availableCommands=[
                            {"name": name, "description": f"Native {name}", "input": None}
                            for name in (
                                "status",
                                "compact",
                                "skills",
                                "mcp",
                                "plan",
                                "goal",
                                "logout",
                                "$project-skill",
                            )
                            if not (name == "compact" and "no-compact" in sys.argv)
                        ],
                    )
                if "early-commands" in sys.argv:
                    await asyncio.sleep(0.01)
                    reply(request, {"sessionId": "test-session", "configOptions": CONFIG})
        elif method == "session/set_config_option":
            if "fallback" not in sys.argv:
                next(item for item in CONFIG if item["id"] == request["params"]["configId"])[
                    "currentValue"
                ] = request["params"]["value"]
            reply(request, {"configOptions": CONFIG})
        elif method == "session/prompt":
            stopped.clear()
            task = asyncio.create_task(prompt(request))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        elif method == "session/cancel":
            stopped.set()
        elif method == "_flowfield/quiesce":
            reply(
                request,
                {
                    "version": 1,
                    "method": "_flowfield/quiesce",
                    "scope": "native-turns-and-terminals",
                    "sessionId": "wrong" if "cleanup-wrong-session" in sys.argv else "test-session",
                    "status": "uncertain" if "cleanup-uncertain" in sys.argv else "confirmed",
                    "reason": None,
                    "checkedThreads": 1,
                    "stoppedTerminals": 0,
                },
            )
        elif method == "session/close":
            if "close-failure" in sys.argv:
                send({"id": request["id"], "error": {"code": -32000, "message": "Close failed"}})
            elif "close-hang" not in sys.argv:
                session_closed = True
                reply(request, {})
        elif request.get("id") in pending:
            pending.pop(request["id"]).set_result(request.get("result"))
    if "close-session" in sys.argv and not session_closed:
        sys.exit(3)
    if "slow-exit" in sys.argv:
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
