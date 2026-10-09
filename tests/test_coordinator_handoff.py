"""Durable source selection precedes native startup; interrupted prompts never replay."""

import base64
import json
from uuid import uuid4

import pytest
from project_fixtures import adopt
from test_coordinator_history import saved_exchange
from test_execution import fixture
from test_storage_migrations import logical_data, raw

from flowfield import migrations, storage
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.application import ProjectSetup, Workspace, now
from flowfield.attachments import Attachments, AttachmentUpload
from flowfield.coordinator_models import CoordinatorSend, CoordinatorTurn
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.reads import size
from flowfield.supervisor import Supervisor


def upload(workspace, name, text):
    return Attachments(workspace).upload(
        "harbor",
        AttachmentUpload(
            name=name,
            mime="text/plain",
            data=base64.b64encode(text.encode()).decode(),
        ),
    )


def test_handoff_is_frozen_before_startup_and_reused_after_restart(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    older = upload(workspace, "older.txt", "OLDER CONTENT MUST NOT BE TRANSFERRED")
    first = saved_exchange(workspace, "Original requirement " + older.href)
    current_attachment = upload(workspace, "current.txt", "CURRENT CONTENT")
    store = CoordinatorStore(workspace)
    conversation = store.new("harbor")
    request = CoordinatorSend(id=uuid4().hex, text="Proceed " + current_attachment.href)
    turn, created = store.reserve("harbor", conversation.id, request)
    assert created and not turn.native_started
    snapshot = store.handoff("harbor", turn.id, budget=200_000)
    assert snapshot["source_before"] == turn.number
    assert snapshot["source_latest"] == first.number
    assert snapshot["recent_conversation"][0]["id"] == first.id
    assert snapshot["recent_conversation"][0]["revision"] == first.activity.revision
    assert snapshot["recent_conversation"][0]["attachments"][0]["id"] == older.id
    assert snapshot["current_attachments"][0]["id"] == current_attachment.id
    assert snapshot["history_is_partial"]
    assert "not_transferred" in snapshot["earlier_attachment_contents"]
    assert "OLDER CONTENT" not in json.dumps(snapshot)
    assert size(snapshot) <= 200_000
    monkeypatch.setattr(
        "flowfield.coordinator_handoff.freeze",
        lambda *a, **kw: pytest.fail("rebuilt accepted history boundary"),
    )
    service = Supervisor(workspace)
    prompt = json.loads(
        service.coordinator._prompt(turn, "scoped-connection", budget=200_000 + 10000)
    )
    assert all(prompt[key] == value for key, value in snapshot.items())
    assert "recent_conversation" not in json.loads(
        service.coordinator._prompt(turn, "scoped-connection", resumed=True)
    )
    with workspace.connection(write=True) as db:
        turn.native_started = True
        store._save(db, turn)
    restored = CoordinatorStore(Workspace(workspace.directory))
    restored.restart()
    assert restored.get("harbor", turn.id).status == "uncertain"
    assert restored.handoff("harbor", turn.id, budget=200_000) == snapshot
    assert service.coordinator.send("harbor", conversation.id, request).id == turn.id
    assert not service.coordinator.jobs
    adopt(workspace, ProjectSetup(path=str(tmp_path / "other")))
    with pytest.raises(ApplicationError, match="not found"):
        restored.handoff("other", turn.id)


def test_handoff_bounds_sources_marks_older_history_and_preserves_originals(tmp_path):
    workspace = fixture(tmp_path).workspace
    first = saved_exchange(workspace, "Older unresolved requirement")
    for _ in range(9):
        saved_exchange(workspace, "Later discussion")
    latest = saved_exchange(workspace, "Opening intent " + "境" * 14000 + " Final requirement")
    store = CoordinatorStore(workspace)
    turn, _ = store.reserve(
        "harbor", store.new("harbor").id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    snapshot = store.handoff("harbor", turn.id, budget=200_000)
    assert size(snapshot) <= 200_000
    assert not snapshot["history_is_partial"]
    assert len(snapshot["recent_conversation"]) == 11
    assert size(snapshot) > 48_000
    limited = store.handoff("harbor", turn.id, budget=5000)
    assert limited["history_is_partial"] and size(limited) <= 5000
    assert limited["recent_conversation"][-1]["abridged_fields"]
    assert snapshot["older_history"] == {"tool": "get_coordinator_history", "before": turn.number}
    source = snapshot["recent_conversation"][-1]
    assert source["human"].startswith("Opening intent") and source["human"].endswith(
        "Final requirement"
    )
    assert source["text_sources"]["human"]["identity"] == latest.id
    assert store.get("harbor", first.id).text == "Older unresolved requirement"
    assert store.get("harbor", latest.id).text == latest.text


def test_failed_snapshot_rolls_back_message_and_attachment_binding(tmp_path, monkeypatch):
    workspace = fixture(tmp_path).workspace
    saved_exchange(workspace, "Prior intent")
    file = upload(workspace, "draft.txt", "Draft contents")
    store = CoordinatorStore(workspace)
    conversation = store.new("harbor")
    request = CoordinatorSend(id=uuid4().hex, text="Review " + file.href)
    with monkeypatch.context() as failure:

        def fail(*args):
            raise ValueError("injected snapshot failure")

        failure.setattr("flowfield.coordinator_store.freeze", fail)
        with pytest.raises(ValueError, match="injected"):
            store.reserve("harbor", conversation.id, request)
    with workspace.connection() as db:
        assert db.execute("SELECT bound FROM attachments WHERE id=?", (file.id,)).fetchone()[0] == 0
        assert (
            db.execute("SELECT id FROM coordinator_turns WHERE id=?", (request.id,)).fetchone()
            is None
        )
    turn, created = store.reserve("harbor", conversation.id, request)
    assert created
    snapshot = store.handoff("harbor", turn.id)
    assert store.reserve("harbor", conversation.id, request) == (turn, False)
    assert store.handoff("harbor", turn.id) == snapshot


def test_schema_47_upgrade_preserves_existing_chat_binding_and_failed_upgrade(
    tmp_path, monkeypatch
):
    with monkeypatch.context() as previous:
        previous.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 47)
        )
        workspace = fixture(tmp_path).workspace
        effective = (
            AgentSettings(workspace)
            .edit(
                "harbor",
                "coordinator",
                AgentSettingsEdit(
                    expected_revision=1,
                    selection=AgentChoice(model="saved-model", effort="low"),
                ),
            )
            .effective
        )
        conversation = CoordinatorStore(workspace).new("harbor")
        old = CoordinatorTurn(
            id=uuid4().hex,
            project_id="harbor",
            conversation_id=conversation.id,
            text="Saved original intent",
            created_at=now(),
            settings=effective,
            status="completed",
            session="resumed",
        )
        with workspace.connection(write=True) as db:
            result = db.execute(
                "INSERT INTO coordinator_turns(id,project_id,conversation_id,status,data) "
                "VALUES (?,?,?,?,?)",
                (old.id, "harbor", conversation.id, old.status, old.model_dump_json()),
            )
            old.number = result.lastrowid
            CoordinatorStore._save(db, old)
            db.execute(
                "INSERT INTO coordinator_sessions VALUES (?,?,?,?,?)",
                ("harbor", "codex", "PRIVATE NATIVE ID", str(tmp_path), None),
            )
    before = logical_data(workspace.directory)
    original = next(m for m in migrations.MIGRATIONS if m.version == 48)

    def fail(db):
        original.apply(db)
        raise ValueError("injected handoff migration failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            migrations,
            "MIGRATIONS",
            (*[m for m in migrations.MIGRATIONS if m.version < 48], Migration(48, fail)),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(workspace.directory)
    assert logical_data(workspace.directory) == before
    with raw(workspace.directory) as db:
        assert storage.version(db) == 47
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='coordinator_handoffs'"
            ).fetchone()
            is None
        )
    upgraded = Workspace(workspace.directory)
    store = CoordinatorStore(upgraded)
    assert store.get("harbor", old.id) == old
    assert store.session("harbor", "codex", str(tmp_path)) == "PRIVATE NATIVE ID"
    with pytest.raises(ApplicationError, match="no frozen handoff"):
        store.handoff("harbor", old.id)
    backup = storage.backups(upgraded.directory)[0]
    assert backup["schema_version"] == 47
    storage.restore(upgraded.directory, backup["id"])
    assert storage.status(upgraded.directory)["schema_version"] == 47
    restored = Workspace(upgraded.directory)
    store = CoordinatorStore(restored)
    assert store.get("harbor", old.id) == old
    new, _ = store.reserve(
        "harbor", conversation.id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    assert (
        store.handoff("harbor", new.id, budget=200_000)["recent_conversation"][0]["human"]
        == old.text
    )
    assert "PRIVATE NATIVE ID" not in json.dumps(store.handoff("harbor", new.id))
    with pytest.raises(ApplicationError, match="Workspace changed"):
        storage.restore(upgraded.directory, backup["id"])
    assert store.get("harbor", new.id).text == "Continue"


def test_unknown_or_exhausted_context_uses_history_tools_without_guessing(tmp_path):
    workspace = fixture(tmp_path).workspace
    saved_exchange(workspace, "An earlier requirement")
    store = CoordinatorStore(workspace)
    turn, _ = store.reserve(
        "harbor", store.new("harbor").id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    for budget in (None, 0, 1, 800):
        handoff = store.handoff("harbor", turn.id, budget=budget)
        assert handoff["history_mode"] == "retrieve"
        assert handoff["recent_conversation"] == []
        assert handoff["source_count"] == 1
        assert handoff["older_history"]["before"] == turn.number


def test_history_boundary_does_not_copy_prose_and_excludes_later_or_late_updates(tmp_path):
    from flowfield.run_activity import ActivityUpdate

    workspace = fixture(tmp_path).workspace
    earlier = saved_exchange(workspace, "Frozen prior intent")
    store = CoordinatorStore(workspace)
    turn, _ = store.reserve(
        "harbor", store.new("harbor").id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    before = store.handoff("harbor", turn.id, budget=200_000)
    store.write(
        "harbor", earlier.id, [ActivityUpdate(key="reply", kind="agent", text="Late replacement")]
    )
    assert store.handoff("harbor", turn.id, budget=200_000) == before

    with workspace.connection(write=True) as db:
        raw = db.execute(
            "SELECT data FROM coordinator_handoffs WHERE turn_id=?", (turn.id,)
        ).fetchone()[0]
        assert "Frozen prior intent" not in raw
        saved = store._get(db, "harbor", turn.id)
        saved.status = "completed"
        store._save(db, saved)
    saved_exchange(workspace, "Later conversation")
    assert store.handoff("harbor", turn.id, budget=200_000) == before


def test_dispatched_budget_survives_restart_and_legacy_snapshots_stay_unchanged(tmp_path):
    workspace = fixture(tmp_path).workspace
    saved_exchange(workspace, "Saved intent " + "界" * 15_000)
    store = CoordinatorStore(workspace)
    turn, _ = store.reserve(
        "harbor", store.new("harbor").id, CoordinatorSend(id=uuid4().hex, text="Continue")
    )
    selected = store.handoff("harbor", turn.id, budget=5000, record=True)
    restored = CoordinatorStore(Workspace(workspace.directory))
    assert restored.handoff("harbor", turn.id, budget=200_000) == selected
    assert restored.handoff("harbor", turn.id, budget=None, record=True) == selected
    assert size(selected) <= 5000 and selected["history_is_partial"]
    legacy = {"version": 1, "recent_conversation": [{"human": "Original frozen text"}]}
    with workspace.connection(write=True) as db:
        db.execute(
            "UPDATE coordinator_handoffs SET data=? WHERE turn_id=?",
            (json.dumps(legacy), turn.id),
        )
    assert restored.handoff("harbor", turn.id, budget=1, record=True) == legacy
