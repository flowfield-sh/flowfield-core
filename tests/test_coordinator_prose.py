"""Full public replies survive activity bounds and upgrades; no native model calls."""

import asyncio
from uuid import uuid4

from coordinator_history_fixtures import saved_exchange
from execution_fixtures import fixture
from fastapi.testclient import TestClient

from flowfield.api import create_app
from flowfield.application import Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.reads import ContextReads
from flowfield.run_activity import ActivityRecorder, ActivityUpdate


def test_streamed_public_reply_survives_activity_trimming_and_restart(tmp_path):
    workspace = fixture(tmp_path).workspace
    turn = saved_exchange(workspace, "Please explain", finish=False)
    store = CoordinatorStore(workspace)
    full = "Opening.\n" + "Long public explanation 境.\n" * 5000 + "Final decision."

    async def record():
        recorder = ActivityRecorder(workspace, "harbor", turn.id, store=store, preserve_prose=True)
        for offset in range(0, len(full), 3000):
            recorder.emit(
                ActivityUpdate(
                    key="reply", kind="agent", text=full[offset : offset + 3000], append=offset > 0
                )
            )
        # Discarding presentation activity must not mark retained prose as lost.
        for index in range(150):
            recorder.emit(ActivityUpdate(key=f"tool-{index}", kind="tool", text="Tool activity"))
        await recorder.close()

    asyncio.run(record())
    with workspace.connection(write=True) as db:
        current = store._get(db, "harbor", turn.id)
        current.status = "completed"
        store._save(db, current)
    assert store.get("harbor", turn.id).activity.omitted
    # Browser history renders retained prose directly, including after the last
    # prose entry has fallen out of bounded tool activity. The source stays bounded.
    with TestClient(create_app(data_dir=workspace.directory), base_url="http://localhost") as api:
        page = api.get("/api/workspaces/harbor/coordinator").json()
        reply = next(
            item for item in page["items"][0]["activity"]["items"] if item["key"] == "reply"
        )
        assert reply["text"] == full and not reply["omitted"]
    assert not any(item.key == "reply" for item in store.get("harbor", turn.id).activity.items)
    reads = ContextReads(Workspace(workspace.directory))
    result, offset = "", 0
    while True:
        page = reads.text("harbor", "coordinator", turn.id, "coordinator", offset=offset)
        assert not page["output_omitted"]
        result += page["text"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert result == full
    next_turn, _ = store.reserve(
        "harbor", turn.conversation_id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    assert (
        store.handoff("harbor", next_turn.id, budget=1_000_000)["recent_conversation"][0][
            "coordinator"
        ]
        == full
    )


def test_browser_restores_latest_reply_without_combining_intermediate_prose(tmp_path):
    workspace = fixture(tmp_path).workspace
    turn = saved_exchange(workspace, "Explain", finish=False)
    store = CoordinatorStore(workspace)
    full = "Final answer.\n" + "Complete retained prose.\n" * 500
    store.write("harbor", turn.id, [ActivityUpdate(key="final", kind="agent", text=full)])
    with workspace.connection(write=True) as db:
        current = store._get(db, "harbor", turn.id)
        current.status = "completed"
        store._save(db, current)
    page = store.page("harbor")
    prose = [item for item in page.items[0].activity.items if item.kind == "agent"]
    assert prose[-1].text == full and not prose[-1].omitted
    assert len(prose) == 2 and prose[0].text == "Public reply Explain"
    # The stored preview remains small and the normal incremental read also restores prose.
    assert store.get("harbor", turn.id).activity.items[-1].omitted
    assert store.page("harbor", after=turn.number).items[0].activity.items[-1].text == full
