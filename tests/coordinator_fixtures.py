"""Reusable isolated setup and scripted peers; no collected test cases."""

import asyncio
import sys
from uuid import uuid4

from managed_local_fixtures import FAKE, configured

from flowfield.agent_models import AgentChoice, AgentSettingsEdit
from flowfield.agent_settings import AgentSettings
from flowfield.coordinator_models import CoordinatorSend


def setup(tmp_path, monkeypatch, scenario="normal", flags=()):
    service, repo, _ = configured(tmp_path, monkeypatch, scenario=scenario)
    monkeypatch.setattr(
        "flowfield.adapters.codex_agent.command",
        lambda directory, env: (
            [
                sys.executable,
                str(FAKE),
                "managed",
                "coordinator",
                "cleanup",
                "close-session",
                *flags,
            ],
            dict(env),
        ),
    )
    AgentSettings(service.workspace).edit(
        "harbor",
        "coordinator",
        AgentSettingsEdit(
            expected_revision=1,
            selection=AgentChoice(model="test-model", effort="low", mode="read-only"),
        ),
    )
    conversation = service.coordinator.store.new("harbor")
    return service, conversation


def message(text="Capture the agreed task"):
    return CoordinatorSend(id=uuid4().hex, text=text)


async def settled(service, turn):
    job = service.coordinator.jobs.get(turn.id)
    if job:
        await asyncio.wait_for(asyncio.shield(job), 15)
    return service.coordinator.store.get("harbor", turn.id)
