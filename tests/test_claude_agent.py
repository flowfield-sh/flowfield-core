"""Compiled official SDK against a scripted native CLI; never a live model."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest

from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.adapters.claude_runtime import executable
from flowfield.adapters.local_process import LocalProcess
from flowfield.agent_models import AgentChoice
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessRegistration

MODEL = "claude-sonnet-5-5"
CHOICE = AgentChoice(harness="claude-code", model=MODEL, effort="low", mode="default", fast=False)


def installed_candidate(tmp_path, *flags):
    native = tmp_path / "native-fixture"
    script = Path(__file__).with_name("fake_claude_cli.py")
    native.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(script))
        + ' "$@"\n'
    )
    native.chmod(0o700)
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    return ClaudeAgent(
        tmp_path,
        {
            "PATH": "",
            "HOME": str(tmp_path),
            "FLOWFIELD_TEST_SCENARIO": flags[0]
            if flags and flags[0] != "no-effort"
            else "complete",
            "FLOWFIELD_TEST_NO_EFFORT": "1" if "no-effort" in flags else "0",
            "FLOWFIELD_TEST_RECORD": str(tmp_path / "native.jsonl"),
        },
        registration=HarnessRegistration(
            harness="claude-code", executable=str(native), config_directory=str(config)
        ),
        choice=CHOICE,
    )


@pytest.mark.parametrize(
    "scenario", ["complete", "foreground", "background", "refused", "runtime-failure"]
)
def test_compiled_sdk_cleanup_tracks_actual_owned_work(tmp_path, scenario):
    async def exercise():
        agent = installed_candidate(
            tmp_path, "background" if scenario == "runtime-failure" else scenario
        )
        events, entered = [], asyncio.Event()

        def activity(value):
            events.append(value)
            if value.kind == "agent":
                entered.set()

        agent.on_activity = activity
        tool = None
        prompt = None
        try:
            executable()  # Missing builds fail the integration check.
            if scenario != "complete":
                tool = await LocalProcess.start(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('ready', flush=True); time.sleep(60)",
                    ],
                    cwd=tmp_path,
                    env={},
                )
                assert await tool.process.stdout.readline() == b"ready\n"
                agent.environment["FLOWFIELD_TEST_TOOL_PID"] = str(tool.process.pid)
            await agent.start([], persistent=True)
            assert await agent.configure(CHOICE) == CHOICE
            prompt = asyncio.create_task(agent.prompt("Synthetic fixture only", None))
            await asyncio.wait_for(entered.wait(), 5)
            if scenario == "runtime-failure":
                # Exact owned runtime handle, never a persisted PID or process name.
                await agent.rpc.owner.close(timeout=0.2)
            elif scenario in {"complete", "background", "refused"}:
                assert (await asyncio.wait_for(prompt, 5))["status"] == "completed"
            confirmed = await agent.stop()
            assert confirmed == (scenario in {"complete", "foreground", "background"})
            if tool:
                if confirmed:
                    await asyncio.wait_for(tool.process.wait(), 5)
                else:
                    assert tool.process.returncode is None
            public = json.dumps([value.model_dump() for value in events])
            assert "Synthetic public answer" in public
            assert "PRIVATE_FIXTURE_THOUGHT" not in public
            records = [
                json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
            ]
            assert any(
                value.get("control", {}).get("subtype") == "get_context_usage" for value in records
            )
        finally:
            await agent.close()
            if tool:
                await tool.close(timeout=0.2)
            if prompt:
                if not prompt.done():
                    prompt.cancel()
                await asyncio.gather(prompt, return_exceptions=True)

    asyncio.run(exercise())


def test_plan_is_rejected_before_native_launch(tmp_path):
    with pytest.raises(ApplicationError):
        ClaudeAgent(
            tmp_path,
            {},
            registration=HarnessRegistration(harness="claude-code"),
            choice=CHOICE.model_copy(update={"mode": "plan"}),
        )


@pytest.mark.parametrize("scenario", ["authentication-error", "result-error", "assistant-error"])
def test_native_api_errors_never_complete_a_turn(tmp_path, scenario):
    async def exercise():
        agent = installed_candidate(tmp_path, scenario)
        try:
            await agent.start([])
            await agent.configure(CHOICE)
            if scenario == "result-error":
                with pytest.raises(ApplicationError, match="Claude Code could not complete"):
                    await agent.prompt("Synthetic API error", None)
            else:
                with pytest.raises(ApplicationError, match="Settings → Harnesses") as failure:
                    await agent.prompt("Synthetic API error", None)
                assert failure.value.code == "claude_authentication_failed"
                assert "resend" in str(failure.value)
        finally:
            await agent.close()
            assert agent.cleanup_confirmed
        records = [
            json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
        ]
        assert sum("input" in record for record in records) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scenario,expected", [("rate-limit", "rate limited"), ("billing-error", "billing")]
)
def test_claude_provider_failures_keep_actionable_category(tmp_path, scenario, expected):
    async def exercise():
        agent = installed_candidate(tmp_path, scenario)
        try:
            await agent.start([])
            await agent.configure(CHOICE)
            with pytest.raises(ApplicationError, match=expected):
                await agent.prompt("Synthetic provider error", None)
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_claude_native_recovery_can_complete_after_an_assistant_error(tmp_path):
    async def exercise():
        agent = installed_candidate(tmp_path, "recovered-error")
        try:
            await agent.start([])
            await agent.configure(CHOICE)
            assert await agent.prompt("Synthetic recovered turn", None) == {"status": "completed"}
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


def test_switch_from_codex_to_claude_keeps_auth_failure_and_recovery_notice(tmp_path, monkeypatch):
    from test_coordinator import message, settled, setup

    from flowfield.agent_models import AgentSettingsEdit
    from flowfield.agent_settings import AgentSettings

    service, conversation = setup(tmp_path, monkeypatch)
    agent = installed_candidate(tmp_path, "authentication-error")

    async def exercise():
        try:
            first = await settled(
                service, service.coordinator.send("harbor", conversation.id, message())
            )
            assert first.status == "completed"
            AgentSettings(service.workspace).edit(
                "harbor", "coordinator", AgentSettingsEdit(expected_revision=2, selection=CHOICE)
            )
            monkeypatch.setattr("flowfield.coordinator.create", lambda *args, **kwargs: agent)
            failed = await settled(
                service,
                service.coordinator.send("harbor", conversation.id, message("Continue in Claude")),
            )
            assert failed.status == "failed" and failed.session == "new"
            assert "Claude Code could not authenticate" in failed.notice
            assert "Settings → Harnesses" in failed.notice
            assert "Not logged in" in failed.activity.model_dump_json()
            assert [turn.id for turn in service.coordinator.store.page("harbor").items] == [
                first.id,
                failed.id,
            ]
        finally:
            await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value", [("model", "wrong-model"), ("effort", "unsupported"), ("fast", True)]
)
def test_invalid_choices_send_no_prompt(tmp_path, field, value):
    async def exercise():
        agent = installed_candidate(tmp_path)
        try:
            await agent.start([])
            with pytest.raises(ApplicationError):
                await agent.configure(CHOICE.model_copy(update={field: value}))
        finally:
            assert await agent.stop()
        assert not any(
            "input" in json.loads(line)
            for line in (tmp_path / "native.jsonl").read_text().splitlines()
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("selection", ["allow", "deny"])
def test_sdk_permissions_scoped_mcp_and_attachments_reach_native_cli(tmp_path, selection):
    from flowfield.adapters.agent_contract import McpHeader, McpServer

    async def exercise():
        agent = installed_candidate(tmp_path, "permission")
        observed = []

        async def decide(request):
            observed.append(request)
            return selection

        server = McpServer(
            name="flowfield-run",
            url="http://127.0.0.1:1/mcp",
            headers=[McpHeader(name="Authorization", value="fixture-scoped-token")],
        )
        try:
            await agent.start([server], persistent=True)
            await agent.configure(CHOICE)
            result = await agent.prompt(
                "Synthetic attachment",
                decide,
                attachments=[
                    {"name": "note.txt", "mime": "text/plain", "data": "cHVibGljIG5vdGU="}
                ],
            )
            assert result["status"] == "completed"
            assert len(observed) == 1 and observed[0].tool_id == "owned-tool"
            assert (
                "echo public" in observed[0].details
                and "PRIVATE_TOOL_INPUT" not in observed[0].details
            )
            records = [
                json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
            ]
            native_permission = next(
                record["permission"] for record in records if "permission" in record
            )
            assert native_permission["response"]["behavior"] == selection
            config = next(record["mcpConfig"] for record in records if "mcpConfig" in record)
            assert config["mcpServers"]["flowfield-run"]["headers"] == {
                "Authorization": "fixture-scoped-token"
            }
            content = next(record["input"]["content"] for record in records if "input" in record)
            assert content[-1] == {"type": "text", "text": "public note"}
        finally:
            assert await agent.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("checkedNativeOwners", True),
        ("quietObservations", 1000),
        ("scope", "process-exit"),
        ("nativeOwnerExited", "true"),
    ],
)
def test_cleanup_receipt_rejects_loose_or_unbounded_proofs(field, value):
    from pydantic import ValidationError

    from flowfield.adapters.claude_agent import CleanupReceipt

    valid = {
        "version": 1,
        "method": "stop",
        "scope": "native-turns-and-tasks",
        "sessionId": "owned",
        "status": "confirmed",
        "reason": None,
        "checkedNativeOwners": 1,
        "stoppedTasks": 0,
        "quietObservations": 2,
        "nativeOwnerExited": True,
    }
    with pytest.raises(ValidationError):
        CleanupReceipt.model_validate({**valid, field: value}, strict=True)


def test_stop_during_native_metadata_never_sends_late_prompt(tmp_path):
    async def exercise():
        agent = installed_candidate(tmp_path)
        agent.environment["FLOWFIELD_TEST_CONTEXT_DELAY"] = "0.3"
        await agent.start([])
        await agent.configure(CHOICE)
        prompt = asyncio.create_task(agent.prompt("Must never reach native input", None))
        await asyncio.sleep(0.05)
        assert await agent.stop()
        assert await prompt == {"status": "stopped"}
        records = [
            json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
        ]
        assert not any("input" in record for record in records)

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "auto", "bypassPermissions"])
def test_native_access_mode_is_explicit_and_confirmed_before_prompt(tmp_path, mode):
    async def exercise():
        agent = installed_candidate(tmp_path)
        agent.choice = CHOICE.model_copy(update={"mode": mode})
        try:
            await agent.start([])
            await agent.configure(agent.choice)
            assert agent.input_tokens_available == 890000
        finally:
            assert await agent.stop()
        records = [
            json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
        ]
        args = next(record["args"] for record in records if "args" in record)
        assert "--disallowedTools" in args
        assert all(
            name in " ".join(args) for name in ["AskUserQuestion", "EnterPlanMode", "ExitPlanMode"]
        )
        assert ("--allow-dangerously-skip-permissions" in args) == (mode == "bypassPermissions")
        assert any(
            record.get("control") == {"subtype": "set_permission_mode", "mode": mode}
            for record in records
        )
        assert not any("input" in record for record in records)

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["mode-refused", "no-auto"])
def test_unavailable_native_mode_never_sends_a_prompt(tmp_path, scenario):
    async def exercise():
        agent = installed_candidate(tmp_path, scenario)
        agent.choice = CHOICE.model_copy(update={"mode": "auto"})
        try:
            with pytest.raises(ApplicationError):
                await agent.start([])
                await agent.configure(agent.choice)
            assert not agent._configured
        finally:
            assert await agent.stop()
        assert not any(
            "input" in json.loads(line)
            for line in (tmp_path / "native.jsonl").read_text().splitlines()
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("scenario", ["complete", "no-auto"])
def test_catalog_exposes_verified_version_and_model_specific_auto(tmp_path, monkeypatch, scenario):
    from flowfield.adapters.claude_agent import model_options

    agent = installed_candidate(tmp_path, scenario)
    for key, value in agent.environment.items():
        monkeypatch.setenv(key, value)
    registration = HarnessRegistration(
        harness="claude-code",
        executable=str(tmp_path / "native-fixture"),
        config_directory=str(tmp_path / "config"),
    )
    models = asyncio.run(model_options(tmp_path, cwd=tmp_path, registration=registration))
    assert len(models) == 1
    assert models[0].id == MODEL and models[0].name == "Sonnet 5.5"
    assert {mode.id for mode in models[0].modes} == {
        "default",
        "acceptEdits",
        "bypassPermissions",
    } | ({"auto"} if scenario == "complete" else set())


@pytest.mark.parametrize(
    "name,expected",
    [
        ("mcp__flowfield_9c2faa714516495184bec40458adaf5f__get_workers", "Flowfield · Get workers"),
        ("mcp__flowfield_9c2faa7145164951__get_workers", "Flowfield · Get workers"),
        ("mcp__other_server__get_workers", "mcp__other_server__get_workers"),
        ("Bash", "Bash"),
    ],
)
def test_claude_scoped_tool_title(name, expected):
    from flowfield.adapters.claude_agent import tool_title

    assert tool_title(name) == expected


@pytest.mark.parametrize(
    "name,resolved,expected",
    [
        ("Sonnet", "claude-sonnet-5-5", "Sonnet 5.5"),
        ("Fable", "claude-fable-5-1", "Fable 5.1"),
        ("Sonnet 4.5", "claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("Custom (1M)", "provider/deployment-v2", "Custom (1M) (provider/deployment-v2)"),
    ],
)
def test_claude_model_labels_keep_verified_identity(name, resolved, expected):
    from flowfield.adapters.claude_agent import model_name

    assert model_name(name, resolved) == expected


@pytest.mark.parametrize("command", ["status", "skills", "mcp", "compact"])
def test_compiled_sdk_commands_use_native_inventory_and_compaction(tmp_path, command):
    async def exercise():
        agent = installed_candidate(tmp_path)
        events = []
        agent.on_activity = events.append
        try:
            await agent.start([], persistent=True)
            await agent.configure(CHOICE)
            assert {c.name for c in await agent.command_options()} == {
                "compact",
                "status",
                "mcp",
                "skills",
            }
            assert await agent.prompt("/" + command, None) == {"status": "completed"}
            assert events
            if command == "skills":
                assert "fixture-skill" in events[-1].text and "Compact" not in events[-1].text
        finally:
            assert await agent.stop()
        records = [
            json.loads(line) for line in (tmp_path / "native.jsonl").read_text().splitlines()
        ]
        users = [r for r in records if r.get("input")]
        assert bool(users) is (command == "compact")

    asyncio.run(exercise())
