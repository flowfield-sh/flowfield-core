"""Reusable model-free workspace/task Usage preview; real persisted accounting owners."""

import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from lifecycle_fixtures import run_report
from task_usage_browser_fixture import seed as seed_task

from flowfield.agent_models import AgentChoice
from flowfield.announcements import Announcements, AnnouncementSettings
from flowfield.application import TaskCreate, TaskEdit, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.execution import Execution
from flowfield.updates import Updates
from flowfield.usage import Usage
from flowfield.usage_models import Consumption, Tokens

CHOICE = AgentChoice(harness="claude-code", model="synthetic-requested-model")


def report(*args, **kwargs):
    with patch("lifecycle_fixtures.AgentChoice", return_value=CHOICE):
        return run_report(*args, **kwargs)


def seed(state: Path):
    result = seed_task(state)
    owner = result["workspace"]
    store = Workspace(state)
    Updates(store).configure(False)
    Announcements(store).configure(
        AnnouncementSettings(work_enabled=False, updates_enabled=False, system_enabled=False)
    )
    usage = Usage(store)
    execution = Execution(store)
    for index, (title, amount, final, archived) in enumerate(
        [
            ("Archived research", 600, True, True),
            ("Interrupted investigation", 100, False, False),
            ("Usage not reported", None, False, False),
        ]
    ):
        task = store.create_task(
            owner, TaskCreate(title=title, body="Synthetic preview only; no native model calls.")
        )
        task = report(store, owner, task.id, expected_revision=task.revision, status="running")
        run = next(r for r in execution.active() if r.task_id == task.id)
        with patch(
            "flowfield.application.now", return_value=f"2026-10-27T{12 + index}:10:00+00:00"
        ):
            usage.start(owner, "run", run.id, run.id)
        with patch(
            "flowfield.application.now", return_value=f"2026-10-27T{12 + index}:40:00+00:00"
        ):
            if amount is not None:
                usage.observe(
                    owner,
                    "run",
                    run.id,
                    Consumption(
                        event_id=uuid4().hex,
                        session_id=run.id,
                        native_version="2.1.293",
                        model="synthetic-reported-model",
                        final=final,
                        counters=Tokens(input=amount, output=0, cache_read=0, cache_write=0),
                    ),
                )
            usage.end(owner, "run", run.id, confirmed=amount is not None)
        if archived:
            task = report(store, owner, task.id, expected_revision=task.revision, status="done")
            store.edit_task(
                owner, task.id, TaskEdit(expected_revision=task.revision, archived=True)
            )
        else:
            execution.finish(owner, run.id, "failed" if amount else "stopped")
    coordinator = CoordinatorStore(store)
    conversation = coordinator.new(owner)
    turn, _ = coordinator.reserve(
        owner,
        conversation.id,
        CoordinatorSend(id=uuid4().hex, text="Synthetic unassigned planning activity."),
    )
    with patch("flowfield.application.now", return_value="2026-10-27T15:50:00+00:00"):
        usage.start(owner, "coordinator_turn", turn.id, turn.id)
    with patch("flowfield.application.now", return_value="2026-10-27T16:10:00+00:00"):
        usage.observe(
            owner,
            "coordinator_turn",
            turn.id,
            Consumption(
                event_id=uuid4().hex,
                session_id=turn.id,
                native_version="2.1.293",
                model=None,
                final=True,
                counters=Tokens(input=270, output=30, cache_read=0, cache_write=0),
            ),
        )
        usage.end(owner, "coordinator_turn", turn.id, confirmed=True)
    # Persist a finished synthetic turn without scheduling any native work.
    with store.connection(write=True) as db:
        turn.status = "completed"
        coordinator._save(db, turn)
    query = urlencode(
        {
            "start": "2026-10-27T06:00:00Z",
            "end": "2026-10-28T06:00:00Z",
            "timezone": "America/Denver",
            "group": "hour",
            "period": "custom",
        }
    )
    result["workspace_url"] = f"/workspaces/{owner}/usage?{query}"
    result["coordinator"] = turn.id
    return result


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]))))
