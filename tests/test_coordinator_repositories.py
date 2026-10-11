"""Workspace repository membership changes native generations, never worker scope."""

import sys
from uuid import uuid4

from project_fixtures import existing_directory
from test_coordinator import setup

from flowfield.adapters.agent_selection import create
from flowfield.adapters.coordinator_directory import for_scope, repositories_for_scope
from flowfield.application import ProjectSetup
from flowfield.coordinator_models import CoordinatorSend
from flowfield.workspaces import Workspaces


def test_native_generation_tracks_member_directories_and_retains_source(tmp_path, monkeypatch):
    service, conversation = setup(tmp_path, monkeypatch)
    ws, store = service.workspace, service.coordinator.store
    managed = for_scope(ws, "harbor")

    def generation():
        turn, _ = store.reserve(
            "harbor", conversation.id, CoordinatorSend(id=uuid4().hex, text="Continue")
        )
        directories = repositories_for_scope(ws, "harbor")
        with ws.connection(write=True) as db:
            value, resumed = store.sessions.prepare(
                db,
                "harbor",
                turn.id,
                turn.settings.choice,
                str(managed),
                None,
                project_directories=tuple(str(path) for path in directories),
            )
            turn.native_started = True
            store._save(db, turn)
        store.sessions.created("harbor", turn.id, value.id, value.session_id or uuid4().hex)
        with ws.connection(write=True) as db:
            store.sessions.dispatch(db, "harbor", turn.id, value.id, turn.settings.choice)
            store.sessions.finish(db, "harbor", turn.id, cleanup_confirmed=True)
            turn.status = "completed"
            store._save(db, turn)
        return value, resumed, turn

    first, resumed, _ = generation()
    assert not resumed and first.project_directories == (ws.project("harbor").path,)
    second, resumed, _ = generation()
    assert resumed and second.id == first.id
    Workspaces(ws).adopt(
        "harbor", ProjectSetup(id="site", path=existing_directory(tmp_path / "site"))
    )
    third, resumed, turn = generation()
    assert not resumed and third.id != first.id and third.source_id == first.id
    assert set(third.project_directories) == {ws.project("harbor").path, ws.project("site").path}
    assert third.cwd == str(managed)
    with ws.connection() as db:
        assert (
            store.sessions.get(db, "harbor", first.id).project_directories
            == first.project_directories
        )
    assert len(store.page("harbor").items) == 3
    # Shared adapter construction does not add member repositories to workers.
    worker = create(
        turn.settings.choice,
        ws.directory,
        tmp_path,
        {"CODEX_PATH": sys.executable, "HOME": str(tmp_path)},
        registration=turn.settings.registration,
    )
    assert worker.project_directories == ()
