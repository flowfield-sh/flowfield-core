"""Native binding generations retain sources and reject obsolete authority."""

import asyncio
import json
from uuid import uuid4

import pytest
from project_fixtures import adopt, finish_legacy_chat, prepare_legacy_chat
from test_execution import fixture
from test_storage_migrations import logical_data, raw

from flowfield import migrations, storage
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.agent_tools import coordinator_scope
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.migrations import Migration
from flowfield.permission_models import PermissionAnswer, PermissionOption
from flowfield.permissions import Permissions
from flowfield.run_activity import ActivityUpdate
from flowfield.supervisor import Supervisor

CODEX = AgentChoice(model="test-model", effort="low", mode="read-only", fast=False)
CLAUDE = AgentChoice(
    harness="claude-code", model="claude-sonnet-5-5", effort="low", mode="default", fast=False
)


def start_coordinator(workspace, *, choice=CODEX, session="native", dispatch=True):
    settings = AgentSettings(workspace)
    current = settings.get("harbor", "coordinator")
    if current.selection != choice:
        settings.edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=current.revision,
                selection=choice,
            ),
        )
    store = CoordinatorStore(workspace)
    turn, _ = store.reserve(
        "harbor",
        store.new("harbor").id,
        CoordinatorSend(id=uuid4().hex, text="Continue the agreed work"),
    )
    with workspace.connection(write=True) as db:
        generation, resumed = store.sessions.prepare(
            db,
            "harbor",
            turn.id,
            choice,
            workspace.project("harbor").path,
            None,
        )
        turn.native_started = True
        store._save(db, turn)
    store.sessions.created("harbor", turn.id, generation.id, session)
    if dispatch:
        with workspace.connection(write=True) as db:
            store.sessions.dispatch(db, "harbor", turn.id, generation.id, choice)
            turn.status = "running"
            turn.session = "resumed" if resumed else "new"
            store._save(db, turn)
    return store, turn, generation


def finish_coordinator(store, turn, *, cleanup=True):
    with store.workspace.connection(write=True) as db:
        turn.status = "stopping"
        store._save(db, turn)
        store.sessions.finish(db, "harbor", turn.id, cleanup_confirmed=cleanup)
        turn.status = "completed" if cleanup else "uncertain"
        store._save(db, turn)


def test_switch_and_switch_back_keep_sources_and_native_bindings(tmp_path):
    workspace = fixture(tmp_path).workspace
    store, first, a = start_coordinator(workspace, session="codex-original")
    finish_coordinator(store, first)
    store, second, b = start_coordinator(workspace, choice=CLAUDE, session="claude-new")
    assert second.conversation_id == first.conversation_id
    assert second.session == "new" and b.source_id == a.id
    assert (
        store.handoff("harbor", second.id, budget=200_000)["recent_conversation"][-1]["id"]
        == first.id
    )
    finish_coordinator(store, second)
    store, third, c = start_coordinator(workspace, session="codex-fresh")
    assert third.session == "new" and c.source_id == b.id and c.id != a.id
    with workspace.connection() as db:
        assert store.sessions.get(db, "harbor", a.id).session_id == "codex-original"
        assert store.sessions.get(db, "harbor", b.id).session_id == "claude-new"
        current, fresh = store.sessions.current(db, "harbor")
        assert current.id == c.id and not fresh
    finish_coordinator(store, third)
    store, fourth, same = start_coordinator(workspace, session="codex-fresh")
    assert fourth.session == "resumed" and same.id == c.id


def test_failed_new_start_keeps_previous_binding_and_switchback_is_fresh(tmp_path):
    workspace = fixture(tmp_path).workspace
    store, first, original = start_coordinator(workspace, session="original")
    finish_coordinator(store, first)
    store, failed, pending = start_coordinator(
        workspace, choice=CLAUDE, session="unmaterialized", dispatch=False
    )
    finish_coordinator(store, failed)
    with workspace.connection() as db:
        current, fresh = store.sessions.current(db, "harbor")
        assert current.id == original.id and fresh
        assert store.sessions.get(db, "harbor", pending.id).status == "failed"
    store, switched_back, successor = start_coordinator(workspace, session="fresh-after-failure")
    assert switched_back.session == "new" and successor.id != original.id


def test_switch_requires_idle_and_unknown_cleanup_requires_confirmation(tmp_path):
    workspace = fixture(tmp_path).workspace
    store, turn, original = start_coordinator(workspace)
    settings = AgentSettings(workspace)
    current = settings.get("harbor", "coordinator")
    with pytest.raises(ApplicationError, match="Stop the coordinator"):
        settings.edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=current.revision,
                selection=CLAUDE,
            ),
        )
    finish_coordinator(store, turn, cleanup=False)
    with pytest.raises(ApplicationError, match="Stop the coordinator"):
        settings.edit(
            "harbor",
            "coordinator",
            AgentSettingsEdit(
                expected_revision=current.revision,
                selection=CLAUDE,
            ),
        )
    assert settings.get("harbor", "coordinator") == current
    store.confirm_stopped("harbor", turn.id)
    store, successor, generation = start_coordinator(workspace, choice=CLAUDE, session="new")
    assert generation.source_id == original.id and successor.session == "new"


def test_old_generation_cannot_write_or_read_through_a_live_grant(tmp_path):
    workspace = fixture(tmp_path).workspace
    store, first, original = start_coordinator(workspace, session="first")
    writer = store.activity_store(original.id)

    def check():
        with workspace.connection() as db:
            store.sessions.actor(db, "harbor", first.id, original.id, statuses=("running",))

    async def exercise():
        grant = await coordinator_scope(Supervisor(workspace), "harbor", check=check)
        assert not (await grant.call("get_board", {})).isError
        finish_coordinator(store, first)
        newer, second, generation = start_coordinator(workspace, choice=CLAUDE, session="second")
        writer.write(
            "harbor", second.id, [ActivityUpdate(key="late", kind="agent", text="OLD OUTPUT")]
        )
        assert not newer.get("harbor", second.id).activity.items
        assert (await grant.call("get_board", {})).isError
        assert (await grant.call("create_task", {"task": {"title": "Late task"}})).isError
        grant.revoke()
        assert generation.id != original.id

    asyncio.run(exercise())


def test_saved_permission_answer_is_not_released_after_turn_ends(tmp_path):
    workspace = fixture(tmp_path).workspace
    store, turn, generation = start_coordinator(workspace)
    owner = Permissions(workspace)
    options = [PermissionOption(id="yes", label="Allow once", kind="allow_once")]

    async def exercise():
        with pytest.raises(ApplicationError, match="native permission session"):
            async with owner.turn(
                "harbor",
                "coordinator",
                session_id="other-native",
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                generation_id=generation.id,
            ):
                pytest.fail("opened mismatched native authority")
        async with owner.turn(
            "harbor",
            "coordinator",
            session_id="native",
            turn_id=turn.id,
            conversation_id=turn.conversation_id,
            generation_id=generation.id,
        ) as live:
            reply = asyncio.create_task(live.request("check", "Run a check", options))
            async with asyncio.timeout(2):
                while not owner.page("harbor").pending:
                    await asyncio.sleep(0.01)
            record = owner.page("harbor").pending[0]
            owner.answer(
                "harbor",
                record.id,
                PermissionAnswer(expected_revision=record.revision, option_id="yes"),
            )
            finish_coordinator(store, turn)
            with pytest.raises(ApplicationError, match="generation changed"):
                await reply
        saved = owner.page("harbor").items[0]
        assert saved.answer == "yes" and not saved.released_at and saved.status == "cancelled"

    asyncio.run(exercise())


@pytest.mark.parametrize("dispatch", [False, True])
def test_crash_after_creation_or_dispatch_preserves_progress_without_replay(tmp_path, dispatch):
    workspace = fixture(tmp_path).workspace
    store, turn, generation = start_coordinator(workspace, dispatch=dispatch)
    snapshot = store.handoff("harbor", turn.id)
    store.restart()
    assert store.get("harbor", turn.id).status == "uncertain"
    with workspace.connection() as db:
        value = store.sessions.get(db, "harbor", generation.id)
        assert value.session_id == "native" and value.status == "uncertain"
        assert value.dispatched == dispatch
        assert not store.owns(db, "harbor", turn.id, generation.id)
    service = Supervisor(workspace)
    assert (
        service.coordinator.send(
            "harbor", turn.conversation_id, CoordinatorSend(id=turn.id, text=turn.text)
        ).id
        == turn.id
    )
    assert not service.coordinator.jobs and store.handoff("harbor", turn.id) == snapshot
    store.confirm_stopped("harbor", turn.id)
    _, successor, next_generation = start_coordinator(
        workspace, session="native" if dispatch else "fresh"
    )
    assert successor.session == ("resumed" if dispatch else "new")
    assert (next_generation.id == generation.id) == dispatch


def test_late_runtime_finalizer_cannot_clear_restart_uncertainty(tmp_path, monkeypatch):
    from test_coordinator import message, settled, setup

    service, conversation = setup(tmp_path, monkeypatch, scenario="permission")

    async def exercise():
        turn = service.coordinator.send("harbor", conversation.id, message())
        try:
            async with asyncio.timeout(10):
                while not service.permissions.page("harbor").pending:
                    await asyncio.sleep(0.01)
            service.coordinator.store.restart()
            uncertain = service.coordinator.store.get("harbor", turn.id)
            service.coordinator.jobs[turn.id].cancel()
            final = await settled(service, turn)
            assert final == uncertain and final.status == "uncertain"
        finally:
            await service.close()

    asyncio.run(exercise())


def test_uncertain_turns_reserve_the_global_coordinator_capacity(tmp_path):
    workspace = fixture(tmp_path).workspace
    held = []
    for index in range(17):
        project = adopt(
            workspace,
            ProjectSetup(
                path=str(tmp_path / f"project-{index}"), task_prefix="GA" + chr(65 + index)
            ),
        )
        AgentSettings(workspace).edit(
            project.id,
            "coordinator",
            AgentSettingsEdit(
                expected_revision=1,
                selection=CODEX,
            ),
        )
        store = CoordinatorStore(workspace)
        conversation = store.new(project.id)
        request = CoordinatorSend(id=uuid4().hex, text="Continue")
        if index == 16:
            with pytest.raises(ApplicationError, match="capacity is reserved"):
                store.reserve(project.id, conversation.id, request)
            store.confirm_stopped(held[0].project_id, held[0].id)
            assert store.reserve(project.id, conversation.id, request)[1]
            break
        turn, _ = store.reserve(project.id, conversation.id, request)
        with workspace.connection(write=True) as db:
            turn.native_started = True
            turn.status = "uncertain"
            store._save(db, turn)
        held.append(turn)


@pytest.mark.parametrize(
    "choice,changed,fresh",
    [
        (CODEX, CODEX.model_copy(update={"model": "other-model"}), False),
        (CLAUDE, CLAUDE, False),
        (CLAUDE, CLAUDE.model_copy(update={"effort": "high"}), True),
        (CLAUDE, CLAUDE.model_copy(update={"model": "other-native-model"}), True),
    ],
)
def test_only_measured_native_reconfiguration_retains_context(tmp_path, choice, changed, fresh):
    workspace = fixture(tmp_path).workspace
    store, first, original = start_coordinator(workspace, choice=choice)
    finish_coordinator(store, first)
    _, next_turn, generation = start_coordinator(
        workspace, choice=changed, session="fresh" if fresh else "native"
    )
    assert (generation.id != original.id) == fresh
    assert next_turn.session == ("new" if fresh else "resumed")


def test_schema_48_binding_upgrade_rolls_back_and_restores_exact_sources(tmp_path, monkeypatch):
    with monkeypatch.context() as previous:
        previous.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version <= 48)
        )
        workspace = fixture(tmp_path).workspace
        AgentSettings(workspace).edit(
            "harbor", "coordinator", AgentSettingsEdit(expected_revision=1, selection=CODEX)
        )
        prepare_legacy_chat(workspace)
        store = CoordinatorStore(workspace)
        turn, _ = store.reserve(
            "harbor",
            store.new("harbor").id,
            CoordinatorSend(id=uuid4().hex, text="Original preserved sources"),
        )
        with workspace.connection(write=True) as db:
            turn.status = "completed"
            turn.session = "resumed"
            store._save(db, turn)
            db.execute(
                "INSERT INTO coordinator_sessions VALUES (?,?,?,?,?)",
                (
                    "harbor",
                    "codex",
                    "private-retained-native",
                    workspace.project("harbor").path,
                    None,
                ),
            )
        snapshot = store.handoff("harbor", turn.id)
        snapshot.pop("welcome", None)
        finish_legacy_chat(workspace)
    before = logical_data(workspace.directory)
    original = next(m for m in migrations.MIGRATIONS if m.version == 49)

    def fail(db):
        original.apply(db)
        raise ValueError("after actual binding replacement")

    with monkeypatch.context() as failure:
        failure.setattr(
            migrations,
            "MIGRATIONS",
            (*[m for m in migrations.MIGRATIONS if m.version < 49], Migration(49, fail)),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(workspace.directory)
    assert logical_data(workspace.directory) == before
    with raw(workspace.directory) as db:
        assert storage.version(db) == 48
        assert len(db.execute("PRAGMA table_info(coordinator_sessions)").fetchall()) == 5
    # Database-only migration never launches or detects native tools/accounts.
    monkeypatch.setattr(
        "flowfield.adapters.harness_host.resolve",
        lambda *a, **kw: pytest.fail("native detection during upgrade"),
    )
    upgraded = Workspace(workspace.directory)
    store = CoordinatorStore(upgraded)
    assert store.get("harbor", turn.id) == turn and store.handoff("harbor", turn.id) == snapshot
    assert (
        store.session("harbor", "codex", upgraded.project("harbor").path)
        == "private-retained-native"
    )
    with upgraded.connection() as db:
        generation, fresh = store.sessions.current(db, "harbor")
        assert not fresh and generation.choice == CODEX and generation.created_by is None
        assert generation.dispatched is None
        assert (
            db.execute(
                "SELECT generation_id FROM coordinator_turns WHERE id=?", (turn.id,)
            ).fetchone()[0]
            is None
        )
    assert "private-retained-native" not in json.dumps(snapshot)
    backup = storage.backups(upgraded.directory)[0]
    assert backup["schema_version"] == 48
    storage.restore(upgraded.directory, backup["id"])
    assert storage.status(upgraded.directory)["schema_version"] == 48
    restored = Workspace(upgraded.directory)
    assert CoordinatorStore(restored).handoff("harbor", turn.id) == snapshot
