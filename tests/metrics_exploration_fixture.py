"""Rich, model-free Metrics exploration in a fresh disposable store only."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from lifecycle_fixtures import run_report
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_models import AgentChoice
from flowfield.announcements import Announcements, AnnouncementSettings
from flowfield.application import TaskCreate, Workspace
from flowfield.coordinator_models import CoordinatorSend
from flowfield.coordinator_store import CoordinatorStore
from flowfield.cost_models import PricingEvidence
from flowfield.execution import Execution
from flowfield.results import Results
from flowfield.task_reviews import ReviewVerdict, TaskReview
from flowfield.updates import Updates
from flowfield.usage import Usage
from flowfield.usage_models import Consumption, Tokens
from flowfield.workspaces import WorkspaceCreate, Workspaces

TITLES = [
    "Compare search relevance across the product catalogue",
    "Audit keyboard navigation in checkout",
    "Investigate slow image loading on mobile",
    "Review onboarding copy for first-time buyers",
    "Measure cache effectiveness for product pages",
    "Map accessibility gaps in account settings",
    "Check inventory synchronization edge cases",
    "Compare payment failure recovery flows",
    "Summarize customer feedback on saved lists",
    "Review filters for long product names and narrow screens",
    "Trace duplicate notifications after reconnect",
    "Evaluate delivery estimates across time zones",
    "Audit loading and empty states in search",
    "Investigate interrupted upload recovery",
    "Review the returns journey end to end",
    "Assess contrast in the dark theme",
    "Compare navigation patterns on tablet",
    "Check price formatting across currencies",
    "Review order history pagination",
    "Measure recommendation response times",
    "Investigate stale cart totals after sign-in",
    "Audit error messages in address forms",
    "Explore a simpler product comparison view",
    "Review analytics event coverage",
    "Plan the next accessibility pass",
    "Document search ranking tradeoffs",
    "Evaluate a compact order summary",
    "Investigate missing usage reports",
]
CHOICES = [
    AgentChoice(harness="codex", model="gpt-6.1-sol"),
    AgentChoice(harness="claude-code", model="claude-sonnet-5-5"),
    AgentChoice(harness="pi", model="claude-haiku-5-5"),
    AgentChoice(harness="codex", model="gpt-6-luna"),
]


def seed(state: Path):
    store = Workspace(state)
    owner = (
        Workspaces(store)
        .create(WorkspaceCreate(name="Metrics playground · synthetic data", **TEST_AGENTS))
        .id
    )
    Updates(store).configure(False)
    Announcements(store).configure(
        AnnouncementSettings(work_enabled=False, updates_enabled=False, system_enabled=False)
    )
    execution, usage = Execution(store), Usage(store)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = []

    def record(source, identity, choice, start, seconds, total):
        end = start + timedelta(seconds=seconds)
        with patch("flowfield.application.now", return_value=start.isoformat()):
            usage.start(owner, source, identity, identity)
        with store.connection(write=True) as db:
            db.execute(
                "UPDATE usage_executions SET created_at=? WHERE source_id=?",
                (start.isoformat(), identity),
            )
        codex, pi = choice.harness == "codex", choice.harness == "pi"
        if total is not None:
            for part in range(1, 5):
                amount = total * part // 4
                at = start + timedelta(seconds=seconds * part / 4)
                with patch("flowfield.application.now", return_value=at.isoformat()):
                    usage.observe(
                        owner,
                        source,
                        identity,
                        Consumption(
                            pricing=PricingEvidence(
                                model=choice.model,
                                provider="openai" if codex else "anthropic",
                                input_observed=(total // 4) * 9 // 10,
                                max_input=(total // 4) * 9 // 10,
                            ),
                            event_id=f"demo-{part}",
                            session_id=identity,
                            native_scope_id=identity if codex else None,
                            dispatch_id=identity,
                            order=part,
                            final=part == 4,
                            contract="codex-thread-total/0.164.0"
                            if codex
                            else "pi-session-stats/1"
                            if pi
                            else "claude-model-usage/0.3.293",
                            native_version="synthetic",
                            model=None if codex or pi else choice.model,
                            adapter_version="codex-app-server-v1"
                            if codex
                            else "pi-rpc-v1"
                            if pi
                            else "claude-sdk-v1",
                            scope="native-thread"
                            if codex
                            else "native-session-all-entries"
                            if pi
                            else "query-pipeline-including-native-children",
                            semantics="inclusive-cache" if codex else "disjoint-cache",
                            counters=Tokens(
                                input=amount - amount // 10,
                                output=amount // 10,
                                cache_read=0,
                                cache_write=0,
                                total=amount,
                            ),
                        ),
                    )
        with patch("flowfield.application.now", return_value=end.isoformat()):
            usage.end(owner, source, identity, confirmed=total is not None)

    for index, title in enumerate(TITLES):
        start = today - timedelta(days=27 - index) + timedelta(hours=1, minutes=index % 4 * 8)
        if index == 27:
            start = today - timedelta(days=45)
        with patch("flowfield.application.now", return_value=start.isoformat()):
            task = store.create_task(
                owner,
                TaskCreate(
                    title=title,
                    body="Explore the journey and recommend improvements.\n\n"
                    "This task and its activity are synthetic. No models were called.",
                ),
            )
        tasks.append(task)
        if index in (24, 25, 26):
            continue
        for round_ in range(1 + index % 4):
            choice = CHOICES[(index + round_) % len(CHOICES)]
            at = start + timedelta(minutes=round_ * 43)
            with (
                patch("lifecycle_fixtures.AgentChoice", return_value=choice),
                patch("flowfield.application.now", return_value=at.isoformat()),
            ):
                task = run_report(
                    store, owner, task.id, expected_revision=task.revision, status="running"
                )
            run = next(r for r in execution.active() if r.task_id == task.id)
            # Fixture-only classification; production derives it at claim time.
            if round_:
                run.launch_reason = "correction" if round_ != 3 else "retry"
                with store.connection(write=True) as db:
                    execution._save(db, run)
                    db.execute(
                        "UPDATE usage_executions SET purpose=? WHERE source_id=?",
                        (run.launch_reason, run.id),
                    )
            seconds = 420 + ((index * 173 + round_ * 241) % 2100)
            record(
                "run",
                run.id,
                choice,
                at,
                seconds,
                None if index == 27 else 18000 + (index * 13721 + round_ * 17381) % 320000,
            )
            with patch(
                "flowfield.application.now",
                return_value=(at + timedelta(seconds=seconds)).isoformat(),
            ):
                if index in (8, 17, 27) and round_ == index % 4:
                    execution.finish(owner, run.id, "failed" if index != 27 else "stopped")
                else:
                    task = run_report(
                        store, owner, task.id, expected_revision=task.revision, status="done"
                    )
            if index % 3 != 0 and store.task(owner, task.id).status == "done":
                # Typed terminal review owners keep the demonstration read-only on startup.
                identity = uuid4().hex
                review = TaskReview(
                    id=uuid4().hex,
                    workspace_id=owner,
                    task_id=task.id,
                    result_id=Results(store).page(owner, task.id).items[0].id,
                    verdict=ReviewVerdict(
                        action="approve",
                        summary="Synthetic review complete",
                        evidence="Model-free fixture evidence",
                    ),
                    status="decided",
                    created_at=at.isoformat(),
                    attempt_id=identity,
                )
                review_choice = CHOICES[(index + 1) % len(CHOICES)]
                data = {
                    "id": identity,
                    "role": "reviewer",
                    "status": "completed",
                    "created_at": at.isoformat(),
                    "requested_agent": review_choice.model_dump(),
                    "launch_reason": "assessment",
                    "cleanup_confirmed": True,
                    "decision": {"action": "approve", "summary": "Synthetic review complete"},
                }
                with store.connection(write=True) as db:
                    db.execute(
                        "INSERT INTO task_reviews(id,workspace_id,task_id,result_id,"
                        "binding,status,data) "
                        "VALUES(?,?,?,?,?,'decided',?)",
                        (
                            review.id,
                            owner,
                            task.id,
                            review.result_id,
                            review.id,
                            review.model_dump_json(),
                        ),
                    )
                    db.execute(
                        "INSERT INTO review_attempts(id,review_id,data) VALUES(?,?,?)",
                        (identity, review.id, json.dumps(data)),
                    )
                record(
                    "review_attempt",
                    identity,
                    review_choice,
                    at + timedelta(minutes=5),
                    240 + index * 17,
                    6000 + index * 2191,
                )
        coordinator = CoordinatorStore(store)
        conversation = coordinator.new(owner)
        turn, _ = coordinator.reserve(
            owner,
            conversation.id,
            CoordinatorSend(id=uuid4().hex, text=f"Plan and discuss: {title}. Synthetic activity."),
        )
        record(
            "coordinator_turn", turn.id, CHOICES[0], start, 180 + index * 11, 4500 + index * 1311
        )
        with store.connection(write=True) as db:
            turn.status = "completed"
            coordinator._save(db, turn)
    query = urlencode(
        {
            "start": (today - timedelta(days=29)).isoformat(),
            "end": (today + timedelta(days=1)).isoformat(),
            "timezone": "UTC",
            "group": "day",
            "period": "custom",
        }
    )
    with store.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
    return {
        "workspace": owner,
        "task": tasks[7].id,
        "url": f"/workspaces/{owner}/tasks/{tasks[7].key}",
        "workspace_url": f"/workspaces/{owner}/metrics?{query}",
        "board_url": f"/workspaces/{owner}/tasks",
        "tasks": len(tasks),
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]))))
