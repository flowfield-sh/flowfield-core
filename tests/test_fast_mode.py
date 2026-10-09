"""Explicit fast settings use native capability discovery and frozen attempt choices."""

import asyncio

import pytest
from test_coordinator import message, settled, setup
from test_execution import BASE, fixture

from flowfield.adapters.codex_agent import CodexAgent
from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.errors import ApplicationError
from flowfield.execution_models import SettingsEdit


def test_fast_discovery_apply_clear_and_unsupported_choice(tmp_path, monkeypatch):
    flags = ["fast"]
    service, conversation = setup(tmp_path, monkeypatch, flags=flags)

    async def exercise():
        model = (await service.model_options())[0]
        assert model.fast and "increased usage" in model.fast_description
        settings = AgentSettings(service.workspace)
        for fast in (True, False):
            choice = AgentChoice(model="test-model", effort="low", mode="read-only", fast=fast)
            settings.edit(
                "harbor",
                "coordinator",
                AgentSettingsEdit(
                    expected_revision=settings.get("harbor", "coordinator").revision,
                    selection=choice,
                ),
            )
            monkeypatch.setenv("FLOWFIELD_TEST_FAST", "on" if fast else "off")
            done = await settled(
                service, service.coordinator.send("harbor", conversation.id, message())
            )
            assert done.status == "completed", done.notice
            assert done.applied.choice.fast is fast and done.settings.choice.fast is fast
        monkeypatch.delenv("FLOWFIELD_TEST_FAST")
        flags.clear()
        assert not (await service.model_options(refresh=True))[0].fast
        with pytest.raises(ApplicationError):
            await service.validate_agent_choice(choice.model_copy(update={"fast": True}))
        agent = CodexAgent(service.workspace.directory, tmp_path, {})
        try:
            await agent.start([])
            with pytest.raises(ApplicationError, match="saved model"):
                await agent.configure(choice.model_copy(update={"fast": True}))
        finally:
            await agent.close()
        await service.close()

    asyncio.run(exercise())


def test_worker_speed_defaults_off_with_task_overrides_and_frozen_history(tmp_path):
    execution = fixture(tmp_path)
    current = execution.settings("harbor")
    execution.configure(
        "harbor",
        SettingsEdit(
            expected_revision=current.revision,
            selection=AgentChoice(model="test-model", effort="low"),
        ),
    )
    settings = AgentSettings(execution.workspace)
    assert settings.get("harbor", "worker", "task-0").effective.choice.fast is False
    settings.edit(
        "harbor",
        "worker",
        AgentSettingsEdit(
            expected_revision=1, selection=AgentChoice(model="test-model", effort="low", fast=True)
        ),
        "task-0",
    )
    run = execution.claim("harbor", BASE, {BASE: set()})
    assert run.agent_settings.choice.fast is True
    settings.edit("harbor", "worker", AgentSettingsEdit(expected_revision=2), "task-0")
    assert settings.get("harbor", "worker", "task-0").effective.choice.fast is False
    assert execution.get("harbor", run.id).agent_settings.choice.fast is True
