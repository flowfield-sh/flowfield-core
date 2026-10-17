"""Reusable isolated setup and scripted peers; no collected test cases."""

import shlex
import sys
from pathlib import Path

from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.agent_models import AgentChoice
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
