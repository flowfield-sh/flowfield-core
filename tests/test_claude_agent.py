"""Candidate adapter invariants with scripted ACP; no native inference or login."""

import asyncio
import base64
import json
import shlex
import sys
from pathlib import Path

import pytest

from flowfield.adapters.agent_selection import command_options, create, model_options
from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessRegistration

MODEL = "claude-sonnet-5-5"
CHOICE = AgentChoice(harness="claude-code", model=MODEL, effort="low", mode="default", fast=False)


def candidate(tmp_path, *flags):
    bridge = tmp_path / "candidate bridge"
    peer = Path(__file__).with_name("fake_acp.py")
    bridge.write_text(
        "#!/bin/sh\nexec "
        + " ".join(shlex.quote(v) for v in (sys.executable, str(peer), "claude", "cleanup", *flags))
        + ' "$@"\n'
    )
    bridge.chmod(0o700)
    environment = {
        "HOME": str(tmp_path),
        "FLOWFIELD_TEST_PROMPT_MARKER": str(tmp_path / "prompt-marker"),
        "FLOWFIELD_TEST_MODEL_DRIFT": str(tmp_path / "model-drift"),
        "FLOWFIELD_TEST_META": json.dumps(
            {
                "claudeCode": {
                    "options": {
                        "model": MODEL,
                        "effort": "low",
                        "permissionMode": "default",
                        "persistSession": True,
                        "strictMcpConfig": True,
                        "allowDangerouslySkipPermissions": False,
                        "maxTurns": 4,
                        "maxBudgetUsd": 0.5,
                    }
                }
            }
        ),
        "CLAUDE_AGENT_LOGS": "PRIVATE LOG TARGET",
        "CLAUDE_AGENT_ACP_EXPERIMENTAL_V2": "1",
    }
    registration = HarnessRegistration(harness="claude-code", revision=7, executable=sys.executable)
    return create(
        CHOICE,
        tmp_path,
        tmp_path,
        environment,
        registration=registration,
        claude_proof_bridge=bridge,
    )


def test_candidate_applies_native_choices_and_shares_public_activity_attachments_and_stop(tmp_path):
    async def exercise():
        agent = candidate(tmp_path, "expect-attachments")
        assert isinstance(agent, ClaudeAgent)
        assert agent.launch.registration_revision == 7
        assert "CLAUDE_AGENT_LOGS" not in agent.environment
        assert "CLAUDE_AGENT_ACP_EXPERIMENTAL_V2" not in agent.environment
        activity = []
        agent.on_activity = activity.append
        try:
            await agent.start([], persistent=True)
            assert await agent.configure(CHOICE) == CHOICE
            assert await asyncio.wait_for(
                agent.prompt(
                    '{"mode":"normal"}',
                    None,
                    attachments=[
                        {
                            "name": "notes.txt",
                            "mime": "text/plain",
                            "data": base64.b64encode(b"Preserve the public API.").decode(),
                        },
                        {"name": "screen.png", "mime": "image/png", "data": "iVBORfixture"},
                    ],
                ),
                5,
            ) == {"status": "completed"}
            assert (tmp_path / "prompt-marker").exists()
            assert any(item.kind == "agent" and item.text == "finished" for item in activity)
            assert any(item.context is not None for item in activity)
            assert "PRIVATE" not in json.dumps([item.model_dump() for item in activity])
            assert await agent.command_options() == []
        finally:
            assert await agent.stop()
        assert await agent.prompt("after Stop", None) == {"status": "stopped"}

    asyncio.run(exercise())


@pytest.mark.parametrize("flags", [("wrong-model",), ("wrong-model-session",)])
def test_actual_native_identity_blocks_fallback_before_prompt(tmp_path, flags):
    async def exercise():
        agent = candidate(tmp_path, *flags)
        try:
            with pytest.raises(ApplicationError, match="exact requested native model"):
                await agent.start([], persistent=True)
            assert not (tmp_path / "prompt-marker").exists()
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_native_identity_is_checked_again_before_each_prompt(tmp_path):
    async def exercise():
        agent = candidate(tmp_path)
        try:
            await agent.start([], persistent=True)
            await agent.configure(CHOICE)
            (tmp_path / "model-drift").touch()
            with pytest.raises(ApplicationError, match="exact requested native model"):
                await agent.prompt("must not reach native inference", None)
            assert not (tmp_path / "prompt-marker").exists()
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_unapplied_settings_and_native_mode_fallback_block_dispatch(tmp_path):
    async def exercise():
        agent = candidate(tmp_path, "fallback")
        try:
            await agent.start([], persistent=True)
            with pytest.raises(ApplicationError, match="Apply native settings"):
                await agent.prompt("unconfigured", None)
            with pytest.raises(ApplicationError, match="native effort and access"):
                await agent.configure(CHOICE.model_copy(update={"mode": "acceptEdits"}))
            with pytest.raises(ApplicationError, match="Apply native settings"):
                await agent.prompt("fallback", None)
            assert not (tmp_path / "prompt-marker").exists()
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_uncertain_native_cleanup_is_not_overridden_by_bridge_exit(tmp_path):
    async def exercise():
        agent = candidate(tmp_path, "cleanup-uncertain")
        await agent.start([], persistent=True)
        assert not await agent.stop()
        assert agent.process.returncode is not None
        assert not agent.cleanup_confirmed

    asyncio.run(exercise())


def test_native_fast_setting_is_disabled_before_claiming_applied_false(tmp_path):
    async def exercise():
        agent = candidate(tmp_path, "fast")
        try:
            await agent.start([], persistent=True)
            assert (
                next(item for item in agent.session.config if item["id"] == "fast")["currentValue"]
                == "on"
            )
            await agent.configure(CHOICE)
            assert (
                next(item for item in agent.session.config if item["id"] == "fast")["currentValue"]
                == "off"
            )
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_service_rejects_claude_choices_before_starting_catalog_discovery(tmp_path, monkeypatch):
    from flowfield.application import Workspace
    from flowfield.supervisor import Supervisor

    service = Supervisor(Workspace(tmp_path / "state"))

    async def forbidden(**kwargs):
        raise AssertionError("An unavailable harness must not launch another harness for discovery")

    monkeypatch.setattr(service, "model_options", forbidden)

    async def exercise():
        try:
            with pytest.raises(ApplicationError, match="not available for managed work"):
                await service.validate_agent_choice(CHOICE)
        finally:
            await service.close()

    asyncio.run(exercise())


def test_production_selection_and_discovery_remain_gated(tmp_path):
    registration = HarnessRegistration(harness="claude-code", revision=1)
    with pytest.raises(ApplicationError, match="not available for managed work"):
        create(CHOICE, tmp_path, tmp_path, {})

    async def exercise():
        with pytest.raises(ApplicationError, match="not available for managed work"):
            await model_options(tmp_path, registration=registration)
        with pytest.raises(ApplicationError, match="not available for managed work"):
            await command_options(tmp_path, tmp_path, CHOICE, registration=registration)

    asyncio.run(exercise())


def test_frozen_registration_cannot_route_to_another_harness(tmp_path):
    with pytest.raises(ApplicationError, match="another harness"):
        create(
            CHOICE,
            tmp_path,
            tmp_path,
            {},
            registration=HarnessRegistration(harness="codex", revision=1),
        )


@pytest.mark.parametrize("role", ["coordinator", "worker"])
def test_candidate_uses_existing_role_owners_and_scoped_tools(tmp_path, monkeypatch, role):
    from test_coordinator import message, settled
    from test_managed_local import configured

    from flowfield.adapters.git_workspace import baseline
    from flowfield.agent_models import AgentSettingsEdit
    from flowfield.agent_settings import AgentSettings
    from flowfield.harness_models import HarnessEdit
    from flowfield.harness_settings import HarnessSettings

    service, repo, _ = configured(tmp_path, monkeypatch)
    bridge = tmp_path / "managed candidate"
    argv = [
        sys.executable,
        str(Path(__file__).with_name("fake_acp.py")),
        "managed",
        "claude",
        "cleanup",
    ]
    if role == "coordinator":
        argv.append("coordinator")
    bridge.write_text("#!/bin/sh\nexec " + " ".join(map(shlex.quote, argv)) + ' "$@"\n')
    bridge.chmod(0o700)
    HarnessSettings(service.workspace).edit(
        "claude-code", HarnessEdit(expected_revision=1, executable=sys.executable)
    )
    AgentSettings(service.workspace).edit(
        "harbor",
        role,
        AgentSettingsEdit(expected_revision=1, selection=CHOICE),
        "task-0" if role == "worker" else "",
    )
    monkeypatch.setenv("FLOWFIELD_TEST_MODE", "default")
    observed = []

    def managed(choice, directory, cwd, environment, *, registration=None):
        agent = create(
            choice,
            directory,
            cwd,
            environment,
            registration=registration,
            claude_proof_bridge=bridge,
        )
        observed.append(agent)
        return agent

    monkeypatch.setattr("flowfield.coordinator.create", managed)
    monkeypatch.setattr("flowfield.supervisor.create", managed)

    async def exercise():
        try:
            if role == "coordinator":
                conversation = service.coordinator.store.new("harbor")
                turn = service.coordinator.send("harbor", conversation.id, message())
                outcome = await settled(service, turn)
                assert outcome.status == "completed", outcome.notice
                assert outcome.applied.choice.harness == "claude-code"
                assert (
                    service.workspace.task("harbor", "chat-task").updated_by
                    == f"coordinator:{turn.id}"
                )
                assert not (repo / "result.txt").exists()
            else:
                base = baseline(repo)
                run = service.execution.claim("harbor", base, {base: set()})
                await asyncio.wait_for(service._execute(run, repo), 10)
                outcome = service.execution.get("harbor", run.id)
                assert outcome.status == "in_review", outcome.problem
                assert outcome.applied_agent.harness == "claude-code"
                assert service.execution.local(run.id)["native_cleanup_confirmed"]
            assert len(observed) == 1 and observed[0].cleanup_confirmed
            assert observed[0].launch.registration_revision == 2
        finally:
            await service.close()

    asyncio.run(exercise())
