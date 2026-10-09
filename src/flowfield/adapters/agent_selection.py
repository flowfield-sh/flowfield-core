"""One concrete harness selection owner for both application roles and discovery."""

from collections.abc import Callable, Mapping
from pathlib import Path

from flowfield.adapters.agent_contract import Agent
from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.adapters.claude_agent import model_options as claude_models
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.codex_agent import command_options as codex_commands
from flowfield.adapters.codex_agent import model_options as codex_models
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
    return CodexAgent(directory, cwd, environment, registration=registration)


async def model_options(
    directory: Path,
    *,
    registration: HarnessRegistration | None = None,
    cwd: Path | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[ModelOption]:
    if on_cleanup:
        on_cleanup(True)  # Selection/validation itself starts no native process.
    discover = (
        claude_models if registration and registration.harness == "claude-code" else codex_models
    )
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
    if choice.harness == "claude-code":
        # No Claude slash command has passed its integrated native semantics yet.
        return []
    return await codex_commands(
        directory, cwd, choice, registration=registration, on_cleanup=on_cleanup
    )
