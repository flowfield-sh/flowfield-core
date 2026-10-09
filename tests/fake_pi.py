"""Scripted Pi RPC peer; no provider requests or native configuration writes."""

import json
import os
import sys
import time

scenario = os.environ.get("PI_TEST_SCENARIO", "complete")
models = [
    {
        "provider": provider,
        "id": "shared-model",
        "name": "Shared model",
        "input": ["text", "image"],
        "contextWindow": 10000,
        "maxTokens": 1000,
    }
    for provider in ("first", "second")
]
session = "native-session"
if "--session" in sys.argv:
    session = sys.argv[sys.argv.index("--session") + 1]
state = {
    "sessionId": session,
    "model": models[0],
    "thinkingLevel": "low",
    "isStreaming": False,
    "isCompacting": False,
    "pendingMessageCount": 0,
}


def emit(value):
    print(json.dumps(value), flush=True)


def event(kind, **data):
    emit({"type": kind, **data})


def notify(message):
    event("extension_ui_request", id="notice", method="notify", message=message)


notify("flowfield:ready:v1")
for line in sys.stdin:
    request = json.loads(line)
    method = request["type"]
    if method == "extension_ui_response":
        continue
    log = os.environ.get("PI_TEST_LOG")
    if log:
        with open(log, "a") as output:
            output.write(json.dumps(request) + "\n")
    data = {}
    success = True
    if method == "get_state":
        data = {**state}
        if scenario == "wrong-session":
            data["sessionId"] = "different"
        if scenario == "busy-stop" and not state["isStreaming"]:
            data["pendingMessageCount"] = 1
    elif method == "get_available_models":
        data = {"models": models}
    elif method == "set_model":
        data = next(model for model in models if model["provider"] == request["provider"])
        state["model"] = data
    elif method == "get_available_thinking_levels":
        data = {"levels": ["off", "low", "high"]}
    elif method == "set_thinking_level":
        state["thinkingLevel"] = request["level"] if scenario != "wrong-effort" else "off"
    elif method == "get_session_stats":
        data = {"contextUsage": {"tokens": 2000, "contextWindow": 10000}}
    elif method == "prompt":
        state["isStreaming"] = True
        data = {"disposition": "queued" if scenario == "queued" else "started"}
    elif method == "abort":
        if scenario == "refused":
            success = False
        else:
            state["isStreaming"] = False
            event("agent_settled", aborted=True)
    emit(
        {
            "type": "response",
            "id": request["id"],
            "command": method,
            "success": success,
            "data": data,
        }
    )
    if method == "prompt" and scenario not in {"foreground", "queued"}:
        event("message_start", message={"role": "assistant"})
        event(
            "message_update",
            assistantMessageEvent={"type": "thinking_delta", "delta": "private reasoning"},
        )
        event("message_update", assistantMessageEvent={"type": "text_delta", "delta": "Partial"})
        event(
            "tool_execution_start",
            toolCallId="tool-1",
            toolName="mcp__flowfield_abc123__get_task",
            args={"task_id": "T1"},
        )
        event(
            "tool_execution_end",
            toolCallId="tool-1",
            toolName="mcp__flowfield_abc123__get_task",
            isError=True,
            result={"content": [{"type": "text", "text": "Task missing"}]},
        )
        event(
            "message_end",
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "Final answer"}],
                "stopReason": "error" if scenario == "error" else "stop",
            },
        )
        event("agent_end", messages=[], willRetry=False)
        if scenario == "disconnect":
            sys.exit(0)
        time.sleep(0.04)
        event("message_start", message={"role": "assistant"})
        event(
            "message_end",
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "Settled answer"}],
                "stopReason": "error" if scenario == "error" else "stop",
            },
        )
        state["isStreaming"] = False
        event("agent_settled", aborted=False)
if scenario != "missing-shutdown":
    notify("flowfield:closed:v1")
