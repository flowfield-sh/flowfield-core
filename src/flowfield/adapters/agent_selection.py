"""One concrete harness selection owner for both application roles and discovery."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import assert_never

from flowfield.adapters.agent_contract import COMMANDS, Agent
from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.adapters.claude_agent import model_options as claude_models
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.codex_agent import model_options as codex_models
from flowfield.adapters.pi_agent import PiAgent
from flowfield.adapters.pi_agent import model_options as pi_models
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.harness_models import HarnessRegistration


def create(
    choice: AgentChoice,
    directory: Path,
    cwd: Path,
    environment: Mapping[str, str],
    *,
    registration: HarnessRegistration | None = None,
) -> Agent:
    if registration is not None and registration.harness != choice.harness:
        raise ApplicationError(
            "harness_registration_mismatch", "Frozen host settings belong to another harness.", 409
        )
    if choice.harness == "claude-code":
        return ClaudeAgent(
            cwd,
            environment,
            registration=registration or HarnessRegistration(harness="claude-code", revision=1),
            choice=choice,
        )
    if choice.harness == "codex":
        return CodexAgent(directory, cwd, environment, registration=registration)
    if choice.harness == "pi":
        return PiAgent(
            directory,
            cwd,
            environment,
            registration=registration or HarnessRegistration(harness="pi"),
        )
    assert_never(choice.harness)


async def model_options(
    directory: Path,
    *,
    registration: HarnessRegistration | None = None,
    cwd: Path | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[ModelOption]:
    if on_cleanup:
        on_cleanup(True)  # Selection/validation itself starts no native process.
    kind = registration.harness if registration else "codex"
    if kind == "claude-code":
        discover = claude_models
    elif kind == "codex":
        discover = codex_models
    elif kind == "pi":
        discover = pi_models
    else:
        assert_never(kind)
    return await discover(directory, registration=registration, cwd=cwd, on_cleanup=on_cleanup)


async def command_options(
    directory: Path,
    cwd: Path,
    choice: AgentChoice,
    *,
    registration: HarnessRegistration | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[AgentCommand]:
    if on_cleanup:
        on_cleanup(True)
    if registration is not None and registration.harness != choice.harness:
        raise ApplicationError(
            "harness_registration_mismatch", "Host settings belong to another harness.", 409
        )
    return [item.model_copy() for item in COMMANDS]
