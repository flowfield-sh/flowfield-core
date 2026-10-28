"""Versioned native shapes through adapters and the production ledger, without providers."""

import asyncio
import sys

import pytest
from execution_fixtures import BASE, fixture
from usage_native_fixtures import CHOICES, native

from flowfield.adapters import codex_usage, pi_usage
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.usage import Usage, normalize
from flowfield.usage_models import Tokens, UsageFilter
from flowfield.usage_reads import UsageReads


def codex_owner(tmp_path, *, resumed=False, version="0.164.0"):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    agent = CodexAgent(tmp_path, tmp_path, {"CODEX_PATH": sys.executable, "HOME": str(tmp_path)})
    usage = Usage(execution.workspace)
    usage.bind("harbor", "run", run.id, agent)
    agent.session_id = run.id
    agent.resume = run.id if resumed else None
    agent._usage_version = version
    agent._usage_threads[run.id] = resumed
    usage.start("harbor", "run", run.id, run.id)
    return execution, usage, run, agent


def total(agent, thread, turn, n, **extra):
    agent._event(
        "thread/tokenUsage/updated",
        {
            "threadId": thread,
            "turnId": turn,
            "tokenUsage": {
                "total": {
                    "inputTokens": n,
                    "outputTokens": n // 5,
                    "cachedInputTokens": n // 2,
                    "reasoningOutputTokens": n // 10,
                    "totalTokens": n + n // 5,
                    **extra,
                },
                "last": {"totalTokens": 999999},
                "modelContextWindow": 1000000,
            },
        },
    )


def terminal(agent, thread, turn, status="completed"):
    agent._event("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": status}})


def detail(execution, run):
    return UsageReads(execution.workspace).evidence("harbor", "run", run.id)


def test_codex_replay_children_foreign_scope_late_stop_and_defaulted_components(tmp_path):
    execution, usage, run, agent = codex_owner(tmp_path)

    async def exercise():
        agent._completion = asyncio.get_running_loop().create_future()
        agent._event("turn/started", {"threadId": run.id, "turn": {"id": "root"}})
        total(agent, run.id, "root", 100, cacheWriteInputTokens=0)
        total(agent, run.id, "root", 100, cacheWriteInputTokens=0)
        total(agent, "unrelated", "root", 10000)
        agent._event(
            "thread/started",
            {"thread": {"id": "child", "parentThreadId": run.id, "forkedFromId": None}},
        )
        agent._event("turn/started", {"threadId": "child", "turn": {"id": "child-turn"}})
        total(agent, "child", "child-turn", 50)
        terminal(agent, "child", "child-turn")
        terminal(agent, run.id, "root")
        agent._completion = None
        agent.stopping = True
        usage.end("harbor", "run", run.id, confirmed=True)
        # A legitimate late higher root snapshot contributes only its delta.
        total(agent, run.id, "root", 150, cacheWriteInputTokens=0)
        total(agent, run.id, "unknown-turn", 9000)

    asyncio.run(exercise())
    evidence = detail(execution, run)
    assert evidence.execution.tokens.total == 240  # 180 root + 60 disjoint child
    assert evidence.execution.tokens.cache_write is None  # Native default is not measured zero.
    assert evidence.execution.observed_models == [None]
    assert evidence.execution.role == "worker"  # Native child role does not create a Reviewer.
    assert evidence.execution.coverage == "complete"
    assert {e.observation.native_scope_id for e in evidence.items} == {run.id, "child"}
    usage.rebuild()
    total(agent, run.id, "root", 150, cacheWriteInputTokens=0)
    assert detail(execution, run).execution.tokens.total == 240


def test_codex_resume_replay_is_baseline_and_context_sentinel_never_consumption(tmp_path):
    execution, usage, run, agent = codex_owner(tmp_path, resumed=True)
    # Native replay precedes dispatch and belongs to history, never this attempt.
    total(agent, run.id, "historical", 5000)

    async def exercise():
        agent._completion = asyncio.get_running_loop().create_future()
        agent._event("turn/started", {"threadId": run.id, "turn": {"id": "new"}})
        total(agent, run.id, "new", 5300)
        # Codex TokenUsageInfo::fill_to_context_window synthesizes only totalTokens.
        total(agent, run.id, "new", 0, totalTokens=1000000)
        terminal(agent, run.id, "new")

    asyncio.run(exercise())
    usage.end("harbor", "run", run.id, confirmed=True)
    evidence = detail(execution, run)
    assert evidence.execution.tokens.total == 360
    assert evidence.items[0].baseline.total == 6000
    assert evidence.execution.coverage == "partial"
    assert "inconsistent-total" in evidence.execution.reasons


@pytest.mark.parametrize(
    "resumed,version,reason",
    [(True, "0.164.0", "unknown-baseline"), (False, "0.165.0", "unsupported-version")],
)
def test_codex_unknown_baseline_and_version_do_not_invent_usage(tmp_path, resumed, version, reason):
    execution, _, run, agent = codex_owner(tmp_path, resumed=resumed, version=version)

    async def exercise():
        agent._completion = asyncio.get_running_loop().create_future()
        agent._event("turn/started", {"threadId": run.id, "turn": {"id": "new"}})
        total(agent, run.id, "new", 100)
        terminal(agent, run.id, "new")

    asyncio.run(exercise())
    evidence = detail(execution, run)
    assert evidence.execution.tokens.total is None
    assert reason in evidence.execution.reasons


def test_codex_unknown_reset_freezes_series_and_terminal_replays_do_not_replace_reminder(tmp_path):
    execution, usage, run, agent = codex_owner(tmp_path)

    async def exercise():
        agent._completion = asyncio.get_running_loop().create_future()
        agent._event("turn/started", {"threadId": run.id, "turn": {"id": "first"}})
        total(agent, run.id, "first", 200)
        terminal(agent, run.id, "first")
        total(agent, run.id, "first", 250)
        usage.start("harbor", "run", run.id, run.id)  # Reminder still lacks its own final.
        usage.end("harbor", "run", run.id, confirmed=True)
        assert detail(execution, run).execution.coverage == "partial"
        total(agent, run.id, "first", 10)
        total(agent, run.id, "first", 500)

    asyncio.run(exercise())
    assert detail(execution, run).execution.tokens.total == 300
    assert "unknown-reset-or-regression" in detail(execution, run).execution.reasons


def test_pi_aggregate_scope_omissions_and_unsupported_versions():
    stats = {
        "sessionId": "session",
        "tokens": {"input": 100, "output": 20, "cacheRead": 50, "cacheWrite": 10, "total": 180},
        "cost": 999,
        "sessionFile": "private",
        "model": "configured",
    }
    event = pi_usage.observation(
        stats,
        session="session",
        dispatch="turn",
        version="1.1.0",
        inherited=Tokens(input=0, output=0, cache_read=0, cache_write=0, total=0),
        final=True,
        failed=False,
    )
    assert normalize(event)[0].total == 180
    assert event.model is None and event.counters.reasoning is None
    assert event.limitations == ["native-provider-coverage-unknown"]
    assert "private" not in event.model_dump_json() and "cost" not in event.model_dump_json()
    assert (
        pi_usage.observation(
            stats,
            session="foreign",
            dispatch="turn",
            version="1.1.0",
            inherited=None,
            final=True,
            failed=False,
        )
        is None
    )
    for version, tokens in [
        ("unknown", stats["tokens"]),
        ("1.1.0", {}),
        ("1.1.0", dict.fromkeys(stats["tokens"], 0)),
    ]:
        value = pi_usage.observation(
            {**stats, "tokens": tokens},
            session="session",
            dispatch="turn",
            version=version,
            inherited=None,
            final=True,
            failed=False,
        )
        assert value.gap in {"unsupported-version", "missing-counters"}
    assert codex_usage.counters({"inputTokens": True, "outputTokens": -1}).input is None


def test_pi_pre_dispatch_resume_baseline_excludes_history(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    usage = Usage(execution.workspace)
    agent = native(tmp_path, "pi")
    usage.bind("harbor", "run", run.id, agent)

    async def exercise():
        try:
            await agent.start([], resume="native-session")
            await agent.configure(CHOICES["pi"])
            # The scripted peer returns a snapshot; substitute an inherited full-session
            # total at the same RPC boundary, without loading any native session files.
            call = agent.rpc.call

            async def stats(method, params, **kwargs):
                data = await call(method, params, **kwargs)
                if method == "get_session_stats":
                    data["tokens"] = {
                        k: v + 1000 if k in ("input", "total") else v
                        for k, v in data["tokens"].items()
                    }
                return data

            agent.rpc.call = stats
            await agent._context()
            usage.start("harbor", "run", run.id, agent.session_id)
            await agent.prompt("synthetic", None)
            assert await agent.stop()
            usage.end("harbor", "run", run.id, confirmed=True)
        finally:
            await agent.close()

    asyncio.run(exercise())
    evidence = detail(execution, run)
    assert evidence.execution.tokens.total == 180
    assert evidence.items[0].baseline.total == 1000
    assert UsageReads(execution.workspace).view("harbor", UsageFilter()).totals.partial == 1


def test_codex_fork_baseline_and_late_child_turn_stay_with_owning_execution(tmp_path):
    execution, usage, run, agent = codex_owner(tmp_path)

    async def exercise():
        agent._completion = asyncio.get_running_loop().create_future()
        agent._event("turn/started", {"threadId": run.id, "turn": {"id": "root"}})
        total(agent, run.id, "root", 100)
        agent._event(
            "thread/started",
            {
                "thread": {
                    "id": "fork",
                    "forkedFromId": run.id,
                    "source": {"subAgent": {"thread_spawn": {"parent_thread_id": run.id}}},
                }
            },
        )
        total(agent, "fork", "copied-history", 5000)
        terminal(agent, run.id, "root")
        agent._completion = None
        agent.stopping = True
        # An already bound native child can settle after foreground Stop.
        agent._event("turn/started", {"threadId": "fork", "turn": {"id": "child"}})
        total(agent, "fork", "child", 5100)
        terminal(agent, "fork", "child")
        # An inherited child without a reported baseline is explicit evidence only.
        agent._event("thread/started", {"thread": {"id": "unknown", "parentThreadId": run.id}})
        agent._event("turn/started", {"threadId": "unknown", "turn": {"id": "unknown-child"}})
        total(agent, "unknown", "unknown-child", 9000)
        terminal(agent, "unknown", "unknown-child")
        usage.end("harbor", "run", run.id, confirmed=True)

    asyncio.run(exercise())
    evidence = detail(execution, run)
    assert evidence.execution.tokens.total == 240
    assert evidence.execution.coverage == "partial"
    assert "unknown-baseline" in evidence.execution.reasons
    assert evidence.execution.dispatch_count == 1
