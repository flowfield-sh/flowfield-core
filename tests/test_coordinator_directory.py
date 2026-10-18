"""Managed homes preserve logical identity, history and native cleanup ownership."""

import asyncio

import pytest
from coordinator_generations_fixtures import CODEX
from project_fixtures import existing_directory
from workspace_fixtures import TEST_AGENTS

from flowfield.adapters.coordinator_directory import for_scope, prepare
from flowfield.application import ProjectSetup, Workspace
from flowfield.errors import ApplicationError
from flowfield.harness_settings import HarnessSettings
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, WorkspaceEdit, Workspaces


def test_home_is_lazy_stable_and_independent_of_repository_locations(tmp_path):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    first = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    second = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
    assert not (store.directory / "coordinators").exists()
    home = prepare(store, first.id)
    assert home == store.directory / "coordinators" / first.id
    assert home.stat().st_mode & 0o777 == 0o700
    assert list(home.iterdir()) == []
    for name, path in (("core", tmp_path / "code" / "core"), ("site", tmp_path / "web" / "site")):
        spaces.adopt(first.id, ProjectSetup(id=name, path=existing_directory(path)))
        assert for_scope(store, first.id) == home
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
    space = Workspaces(store).create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
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


def test_command_discovery_uses_same_home_as_coordinator(tmp_path, monkeypatch):
    store = Workspace(tmp_path / "state")
    spaces = Workspaces(store)
    space = spaces.create(WorkspaceCreate(**TEST_AGENTS, name="Product"))
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
