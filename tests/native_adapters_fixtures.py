"""Reusable isolated setup and scripted peers; no collected test cases."""

import sys
from pathlib import Path

from flowfield.adapters.codex_agent import CodexAgent
from flowfield.agent_models import AgentChoice

FAKE = Path(__file__).with_name("fake_native_codex.py")


CHOICE = AgentChoice(model="test-model", effort="low", mode="read-only", fast=False)


async def start(tmp_path, events, *flags, servers=None, resume=None):
    client = CodexAgent(tmp_path, tmp_path, {"CODEX_PATH": sys.executable, "HOME": str(tmp_path)})
    client.command = [sys.executable, str(FAKE), *flags]
    client.on_activity = events.append
    await client.start(servers or [], resume=resume, persistent=True)
    await client.configure(CHOICE)
    return client
