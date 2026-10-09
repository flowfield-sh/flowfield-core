"""Older public exchanges remain bounded, scoped and independently retrievable."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from project_fixtures import adopt
from test_execution import BASE, fixture

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.agent_tools import coordinator_scope, worker_scope
from flowfield.api import create_app
from flowfield.application import ProjectSetup
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.reads import PAGE_BYTES, ContextReads, size
from flowfield.run_activity import ActivityUpdate
from flowfield.supervisor import Supervisor, WorkerBridge


def saved_exchange(workspace, text, *, finish=True):
    settings = AgentSettings(workspace).get("harbor", "coordinator")
    if settings.effective is None:
        AgentSettings(workspace).edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=settings.revision,
                selection=AgentChoice(model="test-model", effort="low"),
            ),
        )
    store = CoordinatorStore(workspace)
    conversation = store.new("harbor")
    turn, _ = store.reserve("harbor", conversation.id, CoordinatorSend(id=uuid4().hex, text=text))
    store.write(
        "harbor",
        turn.id,
        [
            ActivityUpdate(key="reply", kind="agent", text="Public reply " + text),
            ActivityUpdate(key="tool", kind="tool", text="PRIVATE TOOL DETAILS"),
            ActivityUpdate(key="status", kind="status", text="PRIVATE STATUS DETAILS"),
        ],
    )
    if finish:
        with workspace.connection(write=True) as db:
            turn = store._get(db, "harbor", turn.id)
            turn.status = "completed"
            store._save(db, turn)
    return store.get("harbor", turn.id)


def test_older_exchanges_and_exact_text_survive_paging_and_restart(tmp_path):
    execution = fixture(tmp_path)
    text = 'Original requirement 🦉\\"' * 500
    first = saved_exchange(execution.workspace, text)
    for index in range(26):
        saved_exchange(execution.workspace, f"Later exchange {index}")
    reads = ContextReads(execution.workspace, browser_origin="http://localhost:8765")
    collected, before = [], None
    while True:
        result = reads.coordinator_history("harbor", before=before, limit=7)
        assert size(result) <= PAGE_BYTES
        assert "PRIVATE" not in json.dumps(result)
        collected.extend(result["items"])
        before = result["next_cursor"]
        if before is None:
            break
    assert len(collected) == len({item["id"] for item in collected}) == 27
    assert [item["number"] for item in collected] == sorted(
        [item["number"] for item in collected], reverse=True
    )
    source = collected[-1]["text_sources"]["human"]
    assert source["identity"] == first.id
    assert source["url"].startswith("http://localhost:8765/api/context/projects/harbor/text?")
    assert collected[-1]["truncated_fields"]["human"]
    recovered, offset = "", 0
    while True:
        chunk = reads.text(
            "harbor", "coordinator", first.id, "human", source["revision"], offset, 4000
        )
        assert size(chunk) <= PAGE_BYTES
        recovered += chunk["text"]
        offset = chunk["next_offset"]
        if offset is None:
            break
    assert recovered == text
    # Full text means all retained public prose, with loss explicitly recorded.
    reply = reads.text("harbor", "coordinator", first.id, "coordinator")
    assert reply["output_omitted"]
    assert "PRIVATE" not in json.dumps(reply)
    CoordinatorStore(execution.workspace).restart()
    assert (
        reads.coordinator_history("harbor", before=first.number + 1)["items"][0]["id"] == first.id
    )


def test_delivery_gap_marks_history_full_text_and_frozen_handoff_partial(tmp_path):
    execution = fixture(tmp_path)
    store = CoordinatorStore(execution.workspace)
    turn = saved_exchange(execution.workspace, "Short retained text", finish=False)
    store.write(
        "harbor",
        turn.id,
        [
            ActivityUpdate(
                key="stream-gap", kind="status", text="Activity delivery gap", omitted=True
            )
        ],
    )
    with execution.workspace.connection(write=True) as db:
        finished = store._get(db, "harbor", turn.id)
        finished.status = "completed"
        store._save(db, finished)
    reads = ContextReads(execution.workspace)
    assert reads.coordinator_history("harbor")["items"][0]["output_omitted"]
    reply = reads.text("harbor", "coordinator", turn.id, "coordinator")
    assert reply["output_omitted"] and reply["text"] == "Public reply Short retained text"
    fresh, _ = store.reserve(
        "harbor",
        turn.conversation_id,
        CoordinatorSend(id=uuid4().hex, text="Continue with saved evidence"),
    )
    handoff = store.handoff("harbor", fresh.id)
    assert handoff["history_is_partial"] and handoff["recent_conversation"][0]["output_omitted"]


def test_history_scope_validation_and_changing_output(tmp_path):
    execution = fixture(tmp_path)
    turn = saved_exchange(execution.workspace, "Agreed intent", finish=False)
    reads = ContextReads(execution.workspace)
    source = reads.coordinator_history("harbor")["items"][0]
    CoordinatorStore(execution.workspace).write(
        "harbor",
        turn.id,
        [
            ActivityUpdate(key="reply", kind="agent", text="Continued reply", append=True),
        ],
    )
    with pytest.raises(ApplicationError, match="changed"):
        reads.text("harbor", "coordinator", turn.id, "coordinator", source["revision"])
    adopt(execution.workspace, ProjectSetup(path=str(tmp_path / "other")))
    assert reads.coordinator_history("other")["items"] == []
    with pytest.raises(ApplicationError, match="not found"):
        reads.text("other", "coordinator", turn.id, "human")
    for kwargs in ({"limit": 0}, {"limit": 51}, {"before": 0}, {"before": 2**63}):
        with pytest.raises(ApplicationError):
            reads.coordinator_history("harbor", **kwargs)
    for kwargs in ({"field": "launch"}, {"offset": -1}, {"limit": 4001}):
        with pytest.raises(ApplicationError):
            reads.text("harbor", "coordinator", turn.id, **kwargs)


def test_history_http_and_role_scopes(tmp_path):
    execution = fixture(tmp_path)
    turn = saved_exchange(execution.workspace, "Older intent")
    with TestClient(
        create_app(data_dir=execution.workspace.directory), base_url="http://localhost"
    ) as client:
        response = client.get("/api/context/projects/harbor/coordinator-history")
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == turn.id
        text = client.get(response.json()["items"][0]["text_sources"]["human"]["url"])
        assert text.status_code == 200 and text.json()["text"] == "Older intent"
        assert (
            client.get("/api/context/projects/harbor/coordinator-history?limit=0").status_code
            == 400
        )

    async def exercise():
        grant = await coordinator_scope(Supervisor(execution.workspace), "harbor")
        tool = grant.tools["get_coordinator_history"]
        assert "project_id" not in tool.inputSchema["properties"]
        assert tool.annotations.readOnlyHint
        result = await grant.call("get_coordinator_history", {})
        assert not result.isError and result.structuredContent["items"][0]["id"] == turn.id
        denied = await grant.call("get_coordinator_history", {"project_id": "other"})
        assert denied.isError
        full = await grant.call(
            "get_text",
            {
                "resource": "coordinator",
                "identity": turn.id,
                "field": "human",
            },
        )
        assert not full.isError and full.structuredContent["text"] == "Older intent"
        grant.revoke()
        worker_execution = fixture(tmp_path / "worker")
        run = worker_execution.claim("harbor", BASE, {BASE: set()})
        assert run
        worker = worker_scope(WorkerBridge(worker_execution, run, None))
        assert "get_coordinator_history" not in worker.tools and "get_text" not in worker.tools
        assert (await worker.call("get_coordinator_history", {})).isError
        worker.revoke()

    asyncio.run(exercise())
