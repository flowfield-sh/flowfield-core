"""Measure bounded native events before preview truncation, without live models."""

import asyncio
import json

import pytest
from usage_native_fixtures import CHOICES, native


@pytest.mark.parametrize("harness", CHOICES)
def test_completed_native_output_is_measured_once_before_preview(tmp_path, harness):
    async def check():
        agent = native(tmp_path, harness)
        agent.session_id = "owned"
        agent._completion = asyncio.get_running_loop().create_future()
        agent._foreground_id = "turn"
        agent._running = True
        measured = []
        agent.on_context = lambda *event: measured.append(event)
        output = "🦉" * 20000
        if harness == "codex":
            method = "item/completed"
            data = {
                "threadId": "owned",
                "turnId": "turn",
                "item": {
                    "id": "native-tool",
                    "type": "commandExecution",
                    "command": "read",
                    "aggregatedOutput": output,
                    "exitCode": 0,
                },
            }
            agent._event(method, {**data, "threadId": "other"})
            agent._event(method, {**data, "turnId": "other"})
            size = len(output.encode())
        elif harness == "claude-code":
            method = "activity"
            data = {
                "sessionId": "owned",
                "kind": "tool",
                "key": "native-tool",
                "status": "completed",
                "output": output[:16000],
                "outputBytes": len(output.encode()),
            }
            agent._event(method, {**data, "sessionId": "other"})
            size = len(output.encode())
        else:
            method = "tool_execution_end"
            payload = {"content": [{"type": "text", "text": output}]}
            data = {"toolCallId": "native-tool", "toolName": "read", "result": payload}
            size = len(json.dumps(payload, ensure_ascii=False).encode())
        assert measured == []
        agent._event(method, data)
        agent._event(method, data)
        assert measured == [("native_tool_output", size, "native-payload")]
        agent.stopping = True
        agent._event(method, data)
        assert len(measured) == 1
        if harness == "claude-code":
            agent.stopping = False
            data.pop("outputBytes")
            agent._event(method, {**data, "key": "preview-only"})
            assert measured[-1] == (
                "native_tool_output",
                len(output[:16000].encode()),
                "text-preview",
            )

    asyncio.run(check())


def test_native_measurements_have_a_bounded_identity_set(tmp_path):
    agent = native(tmp_path, "codex")
    events = []
    agent.on_context = lambda *event: events.append(event)
    for i in range(4100):
        agent.context_output(str(i), "x")
    assert len(events) == 4097 and events[-1] == ("native_limit", 0, "unknown")
    assert len(agent._context_seen) == 4096
