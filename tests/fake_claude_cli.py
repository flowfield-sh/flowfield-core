"""Scripted native SDK peer for an explicit model-free SDK conformance probe.

No Claude executable, model API, credentials or real project is used. The probe
owns any supplied tool PID as a live LocalProcess handle in the same test run.
"""

import json
import os
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4


def main() -> None:
    if sys.argv[1:3] == ["auth", "status"]:
        print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}))
        return
    scenario = os.environ["FLOWFIELD_TEST_SCENARIO"]
    record = Path(os.environ["FLOWFIELD_TEST_RECORD"])
    tool_pid = int(os.environ.get("FLOWFIELD_TEST_TOOL_PID", "0"))
    session_id = (
        next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--session-id=")), None)
        or sys.argv[sys.argv.index("--session-id") + 1]
        if "--session-id" in sys.argv
        else next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--session-id="))
    )
    tasks = []
    active_prompt = None
    initialize_count = 0
    usage_turn = 0
    pending_permissions = 0

    def emit(message):
        print(json.dumps(message), flush=True)

    def save(message):
        with record.open("a") as output:
            output.write(json.dumps(message) + "\n")

    save({"args": sys.argv[1:]})

    def snapshot():
        emit(
            {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": tasks,
                "session_id": session_id,
                "uuid": str(uuid4()),
            }
        )

    def stop_tool():
        if not tool_pid:
            return
        try:
            os.kill(tool_pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(tool_pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        raise RuntimeError("The explicitly supplied test tool did not exit")

    def finish(cancelled=False, *, is_error=False):
        nonlocal usage_turn
        usage_turn += 1
        emit(
            {
                "type": "result",
                "subtype": "success",
                "stop_reason": "interrupt" if cancelled else "end_turn",
                "is_error": is_error,
                "result": "",
                "errors": [],
                "duration_ms": 0,
                "duration_api_ms": 0,
                "num_turns": 1,
                "total_cost_usd": 0,
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "modelUsage": {
                    "claude-sonnet-5-5": {
                        "inputTokens": 100 * usage_turn,
                        "outputTokens": 20 * usage_turn,
                        "cacheReadInputTokens": 50 * usage_turn,
                        "cacheCreationInputTokens": 10 * usage_turn,
                        "thinkingTokens": 8 * usage_turn,
                        "costUSD": 999,
                        "private": "PRIVATE_USAGE_FIELD",
                    }
                }
                if scenario in {"usage", "usage-stop"}
                else {},
                "permission_denials": [],
                "uuid": str(uuid4()),
                "session_id": session_id,
                "user_message_uuid": active_prompt.get("uuid"),
            }
        )
        emit(
            {
                "type": "system",
                "subtype": "session_state_changed",
                "state": "idle",
                "session_id": session_id,
                "uuid": str(uuid4()),
            }
        )

    if "--mcp-config" in sys.argv:
        save({"mcpConfig": json.loads(sys.argv[sys.argv.index("--mcp-config") + 1])})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "control_request":
            request = message["request"]
            subtype = request["subtype"]
            save({"control": request})
            response = {}
            if subtype == "initialize":
                initialize_count += 1
                response = {
                    "commands": [
                        {
                            "name": "compact",
                            "description": "Compact",
                            "argumentHint": "",
                            "builtin": True,
                        },
                        {
                            "name": "fixture-skill",
                            "description": "Fixture skill",
                            "argumentHint": "",
                        },
                    ],
                    "agents": [],
                    "models": [
                        {
                            "value": "default",
                            "displayName": "Default",
                            "description": "Synthetic",
                            "resolvedModel": "claude-sonnet-5-5",
                            "supportsEffort": os.environ.get("FLOWFIELD_TEST_NO_EFFORT") != "1",
                            "supportedEffortLevels": []
                            if os.environ.get("FLOWFIELD_TEST_NO_EFFORT") == "1"
                            else ["low", "medium", "high"],
                        },
                        {
                            "value": "sonnet",
                            "displayName": "Sonnet",
                            "description": "Synthetic",
                            "resolvedModel": "claude-sonnet-5-5",
                            "supportsEffort": True,
                            "supportsAutoMode": scenario != "no-auto",
                            "supportedEffortLevels": ["low", "medium", "high"],
                        },
                    ],
                    "account": {"apiProvider": "firstParty", "tokenSource": "none"},
                }
            elif subtype == "interrupt":
                response = {"still_queued": []}
                if scenario in {"foreground", "usage-stop"} and active_prompt:
                    stop_tool()
                    finish(cancelled=True)
                    active_prompt = None
            elif subtype == "stop_task":
                if scenario != "refused":
                    stop_tool()
                    tasks = []
                    snapshot()
            elif subtype == "mcp_status":
                response = {"mcpServers": [{"name": "fixture-mcp", "status": "connected"}]}
            elif subtype == "get_context_usage":
                time.sleep(float(os.environ.get("FLOWFIELD_TEST_CONTEXT_DELAY", "0")))
                response = {
                    "model": "claude-sonnet-5-5",
                    "rawMaxTokens": 1000000,
                    "maxTokens": 900000,
                    "totalTokens": 10000,
                }
            elif subtype == "set_permission_mode" and scenario == "mode-refused":
                emit(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "error",
                            "request_id": message["request_id"],
                            "error": "Native policy does not allow this mode",
                        },
                    }
                )
                continue
            elif subtype not in {
                "set_model",
                "set_permission_mode",
                "apply_flag_settings",
                "cancel_async_message",
            }:
                emit(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "error",
                            "request_id": message["request_id"],
                            "error": "Unsupported synthetic control",
                        },
                    }
                )
                continue
            emit(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": message["request_id"],
                        "response": response,
                    },
                }
            )
            if subtype == "initialize" and initialize_count > 1:
                snapshot()
        elif message.get("type") == "control_response":
            save({"permission": message["response"]})
            pending_permissions -= 1
            if pending_permissions > 0:
                continue
            if scenario == "complete":
                emit(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "fixture-tool",
                                    "content": "Synthetic tool result"
                                    + (
                                        "🦉" * 20000
                                        if os.getenv("FLOWFIELD_TEST_LARGE_OUTPUT")
                                        else ""
                                    ),
                                }
                            ],
                        },
                        "parent_tool_use_id": None,
                        "uuid": str(uuid4()),
                        "session_id": session_id,
                    }
                )
            finish()
            active_prompt = None
        elif message.get("type") == "user":
            active_prompt = message
            if scenario in {"usage", "usage-stop"}:
                emit(
                    {
                        "type": "system",
                        "subtype": "init",
                        "session_id": session_id,
                        "claude_code_version": os.environ.get(
                            "FLOWFIELD_TEST_NATIVE_VERSION", "2.1.293"
                        ),
                    }
                )
            save({"input": message["message"]})
            emit(
                {
                    "type": "user",
                    "message": message["message"],
                    "parent_tool_use_id": None,
                    "uuid": message["uuid"],
                    "session_id": session_id,
                    "isReplay": True,
                }
            )
            if scenario in {
                "authentication-error",
                "result-error",
                "assistant-error",
                "rate-limit",
                "billing-error",
                "recovered-error",
            }:
                if scenario != "result-error":
                    emit(
                        {
                            "type": "assistant",
                            "error": "rate_limit"
                            if scenario in {"rate-limit", "recovered-error"}
                            else "billing_error"
                            if scenario == "billing-error"
                            else "authentication_failed",
                            "message": {
                                "id": "fixture-auth-error",
                                "role": "assistant",
                                "content": [
                                    {"type": "text", "text": "Not logged in · Please run /login"}
                                ],
                            },
                            "parent_tool_use_id": None,
                            "uuid": str(uuid4()),
                            "session_id": session_id,
                        }
                    )
                if scenario != "recovered-error":
                    finish(is_error=scenario != "assistant-error")
                    continue
            emit(
                {
                    "type": "assistant",
                    "message": {
                        "id": "fixture-answer",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-sonnet-5-5",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "PRIVATE_FIXTURE_THOUGHT",
                                "signature": "synthetic",
                            },
                            {"type": "text", "text": "Synthetic public answer"},
                            *(
                                [
                                    {
                                        "type": "tool_use",
                                        "id": "fixture-tool",
                                        "name": "Bash",
                                        "input": {"command": "printf synthetic"},
                                    },
                                ]
                                if scenario == "complete"
                                else []
                            ),
                        ],
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    "parent_tool_use_id": None,
                    "uuid": str(uuid4()),
                    "session_id": session_id,
                }
            )
            if scenario in {"permission", "permission-mcp", "permission-parallel"}:
                pending_permissions = 2 if scenario == "permission-parallel" else 1
                for index in range(pending_permissions):
                    emit(
                        {
                            "type": "control_request",
                            "request_id": f"fixture-permission-{index}",
                            "request": {
                                "subtype": "can_use_tool",
                                "tool_name": os.environ.get("FLOWFIELD_TEST_TOOL", "Bash"),
                                "tool_use_id": "owned-tool" if index == 0 else "second-tool",
                                "input": {
                                    "command": "echo public",
                                    "cwd": str(Path.cwd()),
                                    "private": "PRIVATE_TOOL_INPUT",
                                },
                                "permission_suggestions": [],
                            },
                        }
                    )
                continue
            if scenario in {"background", "refused", "runtime-failure"}:
                tasks = [
                    {
                        "task_id": "owned-test-task",
                        "task_type": "local_bash",
                        "description": "Synthetic detached process",
                    }
                ]
                snapshot()
            if scenario == "complete":
                emit(
                    {
                        "type": "control_request",
                        "request_id": "fixture-permission",
                        "request": {
                            "subtype": "can_use_tool",
                            "tool_name": "Bash",
                            "input": {"command": "printf synthetic"},
                            "tool_use_id": "fixture-tool",
                            "permission_suggestions": [],
                        },
                    }
                )
            elif scenario not in {"foreground", "usage-stop"}:
                finish()


if __name__ == "__main__":
    main()
