"""Managed homes preserve logical identity, history and native cleanup ownership."""

import asyncio
import json

import pytest
from coordinator_fixtures import message, settled, setup
from coordinator_generations_fixtures import CODEX, finish_coordinator, start_coordinator
from execution_fixtures import fixture
from project_fixtures import existing_directory
from storage_fixtures import logical_data

from flowfield import migrations
from flowfield.adapters.coordinator_directory import for_project, prepare
from flowfield.application import ProjectSetup, Workspace
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.harness_settings import HarnessSettings
from flowfield.migrations import Migration
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_home_is_lazy_stable_and_independent_of_repository_locations(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    first = spaces.create(WorkspaceCreate(name="Product"))
    second = spaces.create(WorkspaceCreate(name="Product"))
    assert not (store.directory / "coordinators").exists()
    home = prepare(store, first.id)
    assert home == store.directory / "coordinators" / first.id
    assert home.stat().st_mode & 0o777 == 0o700
    assert list(home.iterdir()) == []
    for name, path in (("core", tmp_path / "code" / "core"), ("site", tmp_path / "web" / "site")):
        spaces.adopt(first.id, ProjectSetup(id=name, path=existing_directory(path)))
        assert for_project(store, name) == home
    (home / "scratch.txt").write_text("Keep local scratch")
    spaces.edit(first.id, WorkspaceEdit(expected_revision=1, name="Renamed"))
    restored = Workspace(store.directory)
    assert prepare(restored, first.id) == home
    assert (home / "scratch.txt").read_text() == "Keep local scratch"
    assert prepare(restored, second.id) != home
    assert not (home / ".git").exists()


def test_unknown_workspace_and_unsafe_home_fail_without_touching_projects(tmp_path):
    store = Workspace(tmp_path / "state")
    with pytest.raises(ApplicationError, match="Workspace not found"):
        prepare(store, "../outside")
    assert not (store.directory / "coordinators").exists()
    space = Workspaces(store).create(WorkspaceCreate(name="Product"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.directory / "coordinators").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError, match="could not prepare"):
        prepare(store, space.id)
    assert list(outside.iterdir()) == []
    (store.directory / "coordinators").unlink()
    (store.directory / "coordinators").write_text("Keep unexpected file")
    with pytest.raises(ApplicationError, match="could not prepare"):
        prepare(store, space.id)
    assert (store.directory / "coordinators").read_text() == "Keep unexpected file"


def test_upgrade_starts_fresh_once_in_managed_home_with_saved_chat(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        service, conversation = setup(tmp_path, monkeypatch)

        # Seed persisted pre-upgrade evidence; the current runtime only launches
        # coordinators after the storage owner has applied supported migrations.
        owner, previous, _ = start_coordinator(service.workspace)
        finish_coordinator(owner, previous)
        previous = owner.get("harbor", previous.id)
        asyncio.run(service.close())
        with service.workspace.connection() as db:
            source, _ = service.coordinator.store.sessions.current(db, "harbor")
    assert not (service.workspace.directory / "coordinators").exists()
    upgraded = Workspace(service.workspace.directory)
    assert not (upgraded.directory / "coordinators").exists()
    restored = Supervisor(upgraded)
    captured = []
    from flowfield.coordinator import create as create_agent

    def observe(choice, directory, cwd, environment, **kwargs):
        captured.append((cwd, environment["PWD"]))
        return create_agent(choice, directory, cwd, environment, **kwargs)

    monkeypatch.setattr("flowfield.coordinator.create", observe)

    async def continuation():
        turn = restored.coordinator.send("harbor", conversation.id, message("Continue our work"))
        handoff = restored.coordinator.store.handoff("harbor", turn.id, budget=200_000)
        assert handoff["recent_conversation"][-1]["id"] == previous.id
        prompt = json.loads(restored.coordinator._prompt(turn, "fixture"))
        assert prompt["workspace_id"] == "harbor"
        assert "project_path" not in prompt
        done = await settled(restored, turn)
        assert done.status == "completed" and done.session == "new", done.notice
        with upgraded.connection() as db:
            current, fresh = restored.coordinator.store.sessions.current(db, "harbor")
            assert not fresh and current.source_id == source.id
            assert current.cwd == str(for_project(upgraded, "harbor"))
            assert restored.coordinator.store.sessions.get(db, "harbor", source.id) == source
        resumed = restored.coordinator.send("harbor", conversation.id, message("Check continuity"))
        assert (await settled(restored, resumed)).session == "resumed"
        await restored.close()

    asyncio.run(continuation())
    home = for_project(upgraded, "harbor")
    assert captured == [(home, str(home)), (home, str(home))]
    assert restored.coordinator.store.get("harbor", previous.id) == previous


def test_upgrade_does_not_bypass_uncertain_native_cleanup(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        store = fixture(tmp_path).workspace
        owner, turn, source = start_coordinator(store)
    upgraded = Workspace(store.directory)
    owner = CoordinatorStore(upgraded)
    owner.restart()
    assert owner.get("harbor", turn.id).status == "uncertain"
    with pytest.raises(ApplicationError, match="active turn"):
        owner.reserve("harbor", turn.conversation_id, message())
    owner.confirm_stopped("harbor", turn.id)
    successor, _ = owner.reserve("harbor", turn.conversation_id, message())
    with upgraded.connection(write=True) as db:
        generation, resumed = owner.sessions.prepare(
            db, "harbor", successor.id, CODEX, str(for_project(upgraded, "harbor")), None
        )
        assert not resumed and generation.source_id == source.id


def test_directory_migration_failure_rolls_back_fresh_flag_and_history(tmp_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(
            migrations, "MIGRATIONS", tuple(m for m in migrations.MIGRATIONS if m.version < 55)
        )
        store = fixture(tmp_path).workspace
        owner, turn, source = start_coordinator(store)
        finish_coordinator(owner, turn)
    before = logical_data(store.directory)
    original = next(m for m in migrations.MIGRATIONS if m.version == 57)

    def fail(db):
        original.apply(db)
        raise ValueError("managed home migration failure")

    with monkeypatch.context() as broken:
        broken.setattr(
            migrations,
            "MIGRATIONS",
            tuple(Migration(57, fail) if m.version == 57 else m for m in migrations.MIGRATIONS),
        )
        with pytest.raises(ApplicationError, match="rolled back"):
            Workspace(store.directory)
    assert logical_data(store.directory) == before
    assert not (store.directory / "coordinators").exists()
    upgraded = Workspace(store.directory)
    with upgraded.connection() as db:
        generation, fresh = CoordinatorStore(upgraded).sessions.current(db, "harbor")
        assert fresh and generation.id == source.id


def test_command_discovery_uses_same_home_as_coordinator(tmp_path, monkeypatch):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(name="Product"))
    spaces.adopt(space.id, ProjectSetup(path=existing_directory(tmp_path / "core")))
    service = Supervisor(store)
    captured = []

    async def discover(directory, cwd, choice, **kwargs):
        captured.append(cwd)
        kwargs["on_cleanup"](True)
        return []

    monkeypatch.setattr("flowfield.catalogs.command_options", discover)

    async def exercise():
        registration = HarnessSettings(store).get("codex")
        await service.catalogs.commands(registration, space.id, CODEX)
        await service.close()

    asyncio.run(exercise())
    assert captured == [prepare(store, space.id)]
