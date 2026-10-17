"""Reusable isolated setup and scripted peers; no collected test cases."""

from uuid import uuid4

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.run_activity import ActivityUpdate


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
