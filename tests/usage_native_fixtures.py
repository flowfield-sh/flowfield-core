"""Concrete adapters with deterministic native peers and disposable configuration."""

import os
import shlex
import sys
from pathlib import Path

from claude_agent_fixtures import CHOICE as CLAUDE_CHOICE
from claude_agent_fixtures import installed_candidate

from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.pi_agent import PiAgent
from flowfield.agent_models import AgentChoice
from flowfield.harness_models import HarnessRegistration

CHOICES = {
    "claude-code": CLAUDE_CHOICE,
    "codex": AgentChoice(harness="codex", model="test-model", effort="low", mode="read-only"),
    "pi": AgentChoice(harness="pi", model="first/shared-model", effort="low", mode="full-access"),
}
TOTALS = {"claude-code": 180, "codex": 120, "pi": 180}


def native(tmp_path, harness, *, stop=False):
    if harness == "claude-code":
        return installed_candidate(tmp_path, "usage-stop" if stop else "usage")
    if harness == "codex":
        agent = CodexAgent(
            tmp_path, tmp_path, {"CODEX_PATH": sys.executable, "HOME": str(tmp_path)}
        )
        agent.command = [
            sys.executable,
            str(Path(__file__).with_name("fake_native_codex.py")),
            "usage",
            "cleanup",
            "close-session",
        ]
        return agent
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    executable = tmp_path / "pi"
    executable.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(Path(__file__).with_name("fake_pi.py")))
        + ' "$@"\n'
    )
    executable.chmod(0o755)
    return PiAgent(
        tmp_path,
        tmp_path,
        {**os.environ, "PI_TEST_SCENARIO": "usage-stop" if stop else "usage"},
        registration=HarnessRegistration(
            harness="pi", executable=str(executable), config_directory=str(config)
        ),
    )


def native_factory(tmp_path, harness):
    index = 0

    def create(*args, **kwargs):
        nonlocal index
        index += 1
        directory = tmp_path / f"native-{index}"
        directory.mkdir()
        return native(directory, harness)

    return create
