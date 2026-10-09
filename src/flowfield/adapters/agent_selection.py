"""One concrete harness selection owner for both application roles and discovery."""

from collections.abc import Mapping
from pathlib import Path

from flowfield.adapters.acp_agent import AcpAgent
from flowfield.adapters.claude_agent import ClaudeAgent
from flowfield.adapters.codex_agent import CodexAgent
from flowfield.adapters.codex_agent import command_options as codex_commands
from flowfield.adapters.codex_agent import model_options as codex_models
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.harness_models import HarnessKind, HarnessRegistration


def require_available(harness: HarnessKind) -> None:
    if harness != "codex":
        raise ApplicationError(
            "harness_unavailable",
            "Claude Code is not available for managed work yet. Its integrated proof "
            "and installed bridge are still required.",
            409,
        )


def create(
    choice: AgentChoice,
    directory: Path,
    cwd: Path,
    environment: Mapping[str, str],
    *,
    registration: HarnessRegistration | None = None,
    claude_proof_bridge: Path | None = None,
) -> AcpAgent:
    if registration is not None and registration.harness != choice.harness:
        raise ApplicationError(
            "harness_registration_mismatch", "Frozen host settings belong to another harness.", 409
        )
    if choice.harness == "claude-code" and claude_proof_bridge is not None:
        return ClaudeAgent(
            cwd,
            environment,
            registration=registration or HarnessRegistration(harness="claude-code", revision=1),
            bridge=claude_proof_bridge,
            choice=choice,
        )
    require_available(choice.harness)
    return CodexAgent(directory, cwd, environment, registration=registration)


async def model_options(
    directory: Path, *, registration: HarnessRegistration | None = None
) -> list[ModelOption]:
    require_available(registration.harness if registration else "codex")
    return await codex_models(directory, registration=registration)


async def command_options(
    directory: Path,
    cwd: Path,
    choice: AgentChoice,
    *,
    registration: HarnessRegistration | None = None,
) -> list[AgentCommand]:
    require_available(choice.harness)
    if registration is not None and registration.harness != choice.harness:
        raise ApplicationError(
            "harness_registration_mismatch", "Host settings belong to another harness.", 409
        )
    return await codex_commands(directory, cwd, choice, registration=registration)
