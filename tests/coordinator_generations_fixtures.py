"""Reusable isolated setup and scripted peers; no collected test cases."""

from uuid import uuid4

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore

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
