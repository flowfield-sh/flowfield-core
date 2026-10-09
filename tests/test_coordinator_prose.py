"""Full public replies survive activity bounds and upgrades; no native model calls."""

import asyncio
import json
from uuid import uuid4

import pytest
from test_coordinator_history import saved_exchange
from test_execution import fixture
from test_storage_migrations import logical_data

from flowfield import migrations
from flowfield.application import Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
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


def test_prose_migration_preserves_retained_text_and_omissions_and_rolls_back(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 50)
        )
        # The old writer persisted only the activity projection.
        old.setattr("flowfield.coordinator_store.write_prose", lambda *args: None)
        workspace = fixture(tmp_path).workspace
        turn = saved_exchange(workspace, "Retained " + "x" * 10000)
    before = logical_data(workspace.directory)
    migration = migrations.MIGRATIONS[-1]

    def fail(db):
        migration.apply(db)
        raise ValueError("Injected transcript upgrade failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:-1], migrations.Migration(51, fail))
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(workspace.directory)
    assert logical_data(workspace.directory) == before
    upgraded = Workspace(workspace.directory)
    with upgraded.connection() as db:
        stored = json.loads(
            db.execute("SELECT data FROM coordinator_prose WHERE turn_id=?", (turn.id,)).fetchone()[
                0
            ]
        )
    assert stored["omitted"]
    assert stored["items"][0]["text"] == turn.activity.items[0].text
    assert "PRIVATE TOOL DETAILS" not in json.dumps(stored)
