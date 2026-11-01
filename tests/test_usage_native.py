"""Compiled pinned SDK -> concrete adapter -> real lifecycle owners -> durable reads."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from claude_agent_fixtures import installed_candidate
from coordinator_fixtures import message, settled, setup
from managed_local_fixtures import configured
from result_fixtures import current
from task_reviews_fixtures import configured as review_configured
from usage_native_fixtures import CHOICES, TOTALS, native, native_factory

from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.adapters.git_workspace import baseline
from flowfield.agent_models import AgentSettingsEdit
from flowfield.application import Workspace
from flowfield.context_measurements import ContextMeasurements
from flowfield.coordinator_models import CoordinatorTaskSelection
from flowfield.execution_models import SettingsEdit
from flowfield.reviewer_settings import ReviewerEdit
from flowfield.supervisor import Supervisor
from flowfield.usage_models import UsageFilter
from flowfield.usage_reads import UsageReads


@pytest.mark.parametrize("legacy", [False, True])
def test_claude_instructions_resume_or_cross_upgrade_boundary(tmp_path, monkeypatch, legacy):
    service, conversation = setup(tmp_path, monkeypatch)
    from flowfield.coordinator_settings import CoordinatorSettings

    settings = CoordinatorSettings(service.workspace)
    settings.edit(
        "harbor",
        AgentSettingsEdit(
            expected_revision=settings.get("harbor").revision, selection=CHOICES["claude-code"]
        ),
    )
    peer = tmp_path / "claude-peer"
    peer.mkdir()
    monkeypatch.setattr(
        "flowfield.coordinator.create", lambda *args, **kwargs: installed_candidate(peer, "usage")
    )
    original = ClaudeAgent.session_instructions
    if legacy:
        monkeypatch.setattr(ClaudeAgent, "session_instructions", lambda *args, **kwargs: False)

    async def exercise():
        monkeypatch.setattr(service, "validate_agent_choice", AsyncMock())
        first = await settled(
            service, service.coordinator.send("harbor", conversation.id, message())
        )
        assert first.status == "completed", first.notice
        with service.workspace.connection(write=True, notify=False) as db:
            generation, _ = service.coordinator.store.sessions.current(db, "harbor")
            identity, session = generation.id, generation.session_id
            if legacy:
                # Simulate the pre-boundary store with an existing inline session.
                db.execute("PRAGMA user_version=94")
        await service.coordinator.close()
        monkeypatch.setattr(ClaudeAgent, "session_instructions", original)
        restarted = Supervisor(Workspace(service.workspace.directory))
        monkeypatch.setattr(restarted, "validate_agent_choice", AsyncMock())
        second = await settled(
            restarted, restarted.coordinator.send("harbor", conversation.id, message("Continue"))
        )
        assert second.status == "completed", second.notice
        assert second.session == ("new" if legacy else "resumed")
        with restarted.workspace.connection() as db:
            retained, _ = restarted.coordinator.store.sessions.current(db, "harbor")
            assert ((retained.id, retained.session_id) == (identity, session)) is not legacy
            if legacy:
                assert retained.source_id == identity
                assert (
                    restarted.coordinator.store.sessions.get(db, "harbor", identity).session_id
                    == session
                )
        records = [json.loads(line) for line in (peer / "native.jsonl").read_text().splitlines()]
        payload = json.loads(
            [r["input"]["content"][0]["text"] for r in records if "input" in r][-1]
        )
        assert "instructions" not in payload
        if legacy:
            assert "Capture the agreed task" in json.dumps(payload)
        measure = ContextMeasurements(
            restarted.workspace, "harbor", "coordinator_turn", second.id
        ).read()
        assert any(row["kind"] == "session_instructions" for row in measure["items"])
        third = await settled(
            restarted,
            restarted.coordinator.send("harbor", conversation.id, message("Continue again")),
        )
        assert third.status == "completed" and third.session == "resumed"
        with restarted.workspace.connection() as db:
            latest, fresh = restarted.coordinator.store.sessions.current(db, "harbor")
            assert latest.id == retained.id and not fresh
        assert restarted.coordinator.store.get("harbor", first.id).text == first.text
        await restarted.coordinator.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "harness,version",
    [
        ("codex", "0.164.0"),
        ("codex", "0.165.0"),
        ("codex", "9.0.0"),
        ("codex", "9.0.0-beta.1+build.7"),
        ("claude-code", "2.1.293"),
        ("claude-code", "2.1.295"),
        ("claude-code", "9.0.0"),
        ("pi", None),
        ("pi", "1.4.0"),
        ("pi", "9.0.0"),
        ("pi", "9.0.0-beta.1+build.7"),
    ],
)
def test_worker_reminder_through_native_adapter_is_one_attempt(
    tmp_path, monkeypatch, harness, version
):
    service, repo, _ = configured(tmp_path, monkeypatch)
    settings = service.execution.settings("harbor")
    service.execution.configure(
        "harbor", SettingsEdit(expected_revision=settings.revision, selection=CHOICES[harness])
    )
    monkeypatch.setattr(
        "flowfield.supervisor.create", native_factory(tmp_path, harness, version=version)
    )
    base = baseline(repo)
    run = service.execution.claim("harbor", base, {base: set()})
    asyncio.run(service._execute(run, repo))
    detail = UsageReads(service.workspace).evidence("harbor", "run", run.id)
    assert detail.execution.tokens.total == TOTALS[harness] * 2  # cumulative reports, not their sum
    assert detail.execution.dispatch_count == 2
    assert detail.execution.coverage == ("partial" if harness == "pi" else "complete")
    assert detail.execution.ended_at is not None
    measure = ContextMeasurements(service.workspace, "harbor", "run", run.id).read()
    assert sum(r["events"] for r in measure["totals"] if r["kind"] == "prompt") == 2
    assert all(r["bytes"] > 0 for r in measure["items"] if r["kind"] == "prompt")
    assert detail.execution.role == "worker"
    assert service.execution.get("harbor", run.id).status == "failed"  # no managed result
    assert "PRIVATE_USAGE_FIELD" not in detail.model_dump_json()
    assert "costUSD" not in detail.model_dump_json()
    if version is not None:
        assert {item.observation.native_version for item in detail.items} == {version}


@pytest.mark.parametrize("harness", CHOICES)
def test_coordinator_mixed_selected_task_is_related_only(tmp_path, monkeypatch, harness):
    service, conversation = setup(tmp_path, monkeypatch)
    # Coordinator settings use their existing explicit owner.
    from flowfield.coordinator_settings import CoordinatorSettings

    config = CoordinatorSettings(service.workspace).get("harbor")
    CoordinatorSettings(service.workspace).edit(
        "harbor", AgentSettingsEdit(expected_revision=config.revision, selection=CHOICES[harness])
    )
    monkeypatch.setattr("flowfield.coordinator.create", native_factory(tmp_path, harness))
    monkeypatch.setattr(service, "validate_agent_choice", AsyncMock())
    task = service.workspace.tasks("harbor")[0]

    async def run():
        request = message("Selected task plus unrelated workspace planning")
        request.task_context = CoordinatorTaskSelection(
            task_id=task.id, task_revision=task.revision
        )
        turn = service.coordinator.send("harbor", conversation.id, request)
        return await settled(service, turn)

    turn = asyncio.run(run())
    detail = UsageReads(service.workspace).evidence("harbor", "coordinator_turn", turn.id)
    assert detail.execution.tokens.total == TOTALS[harness], turn.notice
    measure = ContextMeasurements(service.workspace, "harbor", "coordinator_turn", turn.id).read()
    assert any(r["kind"] == "prompt" and r["bytes"] > 0 for r in measure["items"])
    assert detail.execution.task_id is None
    assert detail.execution.related_task_id == task.id
    task_view = UsageReads(service.workspace).view("harbor", UsageFilter(), task=task.id)
    assert task_view.totals.tokens.total is None
    assert task_view.related_coordinator_attempts == 1
    assert (
        UsageReads(service.workspace).view("harbor", UsageFilter()).totals.tokens.total
        == TOTALS[harness]
    )


@pytest.mark.parametrize("harness", CHOICES)
def test_reviewer_native_consumption_owned_by_attempt(tmp_path, monkeypatch, harness):
    service, _, run, settings, reviews = review_configured(tmp_path)
    configured = settings.get("harbor")
    settings.configure(
        "harbor",
        ReviewerEdit(
            expected_revision=configured.revision, enabled=True, selection=CHOICES[harness]
        ),
    )
    monkeypatch.setattr(
        "flowfield.native_review_execution.create", native_factory(tmp_path, harness)
    )
    monkeypatch.setattr(service, "validate_agent_choice", AsyncMock())

    async def assess():
        await service.review_execution.schedule()
        await asyncio.gather(*list(service.jobs.values()))

    asyncio.run(assess())
    review = reviews.latest("harbor", run.task_id)
    assert review is not None
    detail = UsageReads(service.workspace).evidence("harbor", "review_attempt", review.attempt_id)
    assert detail.execution.tokens.total == TOTALS[harness]
    measure = ContextMeasurements(
        service.workspace, "harbor", "review_attempt", review.attempt_id
    ).read()
    assert any(r["kind"] == "prompt" and r["bytes"] > 0 for r in measure["items"])
    assert detail.execution.role == "reviewer"
    assert detail.execution.parent_id == review.id
    assert detail.execution.project_id == run.project_id
    assert detail.execution.coverage == ("partial" if harness == "pi" else "complete")
    assert current(service).approved_at is None
    assert (
        UsageReads(service.workspace)
        .view("harbor", UsageFilter(), task=run.task_id)
        .totals.tokens.total
        == TOTALS[harness]
    )


def test_javascript_usage_contract():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "runtimes/claude-sdk/usage.test.mjs"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("harness", CHOICES)
def test_native_stop_retains_bound_usage_without_restoring_worker_authority(tmp_path, harness):
    from execution_fixtures import BASE, fixture

    from flowfield.execution_models import RunAction
    from flowfield.usage import Usage

    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    usage = Usage(execution.workspace)
    agent = native(tmp_path, harness, stop=True)

    async def exercise():
        entered = asyncio.Event()
        agent.on_activity = lambda value: entered.set() if value.kind == "agent" else None
        usage.bind("harbor", "run", run.id, agent)
        task = None
        try:
            await agent.start([])
            await agent.configure(CHOICES[harness])
            started = execution.started("harbor", run.id)
            usage.start("harbor", "run", run.id, agent.session_id)
            task = asyncio.create_task(agent.prompt('{"mode":"wait"}', None))
            await asyncio.wait_for(entered.wait(), 5)
            execution.stop_requested(
                "harbor", run.id, RunAction(expected_revision=started.revision)
            )
            assert await agent.stop()
            assert (await task)["status"] == "stopped"
            usage.end("harbor", "run", run.id, confirmed=True)
            assert execution.get("harbor", run.id).status == "stopping"
            detail = UsageReads(execution.workspace).evidence("harbor", "run", run.id)
            assert detail.execution.tokens.total == TOTALS[harness]
            assert len(detail.items) >= 1
        finally:
            await agent.close()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
