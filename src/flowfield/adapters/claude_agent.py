"""Internal Claude adapter for integrated proof, not a selectable installed runtime.

The explicit development bridge is required. Native configuration/login stay owned
by Claude Code; this adapter sends no direct model API requests. Model identity is
verified through the candidate's session-fenced native context RPC before inference.
"""

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer
from pydantic import BaseModel

from flowfield.adapters.acp_agent import AcpAgent
from flowfield.adapters.acp_session import (
    AcpSession,
    PermissionHandler,
    activity_locations,
    bounded_details,
)
from flowfield.adapters.claude_cleanup import CLAUDE_SHUTDOWN_TIMEOUTS, quiesce, require_cleanup
from flowfield.adapters.harness_host import launch_environment, resolve
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessRegistration


def claude_activity_details(tool: BaseModel) -> str:
    """Public execution locations only; never arbitrary MCP inputs or SDK messages."""
    raw = tool.model_dump(by_alias=True, exclude_none=True).get("rawInput")
    parts = [activity_locations(tool)]
    if isinstance(raw, dict):
        for key in ("command", "cwd", "file_path", "path"):
            if isinstance(raw.get(key), str):
                parts.append(f"{key}: {raw[key]}")
    return bounded_details("\n".join(filter(None, parts)))


class ClaudeAgent(AcpAgent):
    def __init__(
        self,
        cwd: Path,
        environment: Mapping[str, str],
        *,
        registration: HarnessRegistration,
        bridge: Path,
        choice: AgentChoice,
    ):
        if registration.harness != "claude-code" or choice.harness != "claude-code":
            raise ValueError("Claude requires a Claude registration and choice")
        if not choice.mode:
            raise ApplicationError("agent_mode_required", "Choose a native access mode.", 409)
        if not bridge.is_absolute() or not bridge.is_file() or not os.access(bridge, os.X_OK):
            raise ApplicationError(
                "claude_bridge_unavailable", "Build the development bridge.", 409
            )
        super().__init__(
            AcpSession(
                self._event,
                cleanup=quiesce,
                shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
                activity_projection=claude_activity_details,
            )
        )
        self.launch = resolve(registration, environment)
        self.environment = launch_environment(self.launch, environment)
        self.environment.pop("CLAUDE_AGENT_LOGS", None)
        self.environment.pop("CLAUDE_AGENT_ACP_EXPERIMENTAL_V2", None)
        self.launch.bridge_executable = str(bridge)
        self.launch.bridge_version = "0.88.0-flowfield.proof.3"
        self.command = [str(bridge), "--flowfield-proof-cleanup"]
        self.cwd = cwd
        self.choice = choice.model_copy(deep=True)
        self.native_model: str | None = None
        self._configured = False

    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        self.cleanup_confirmed = False
        try:
            await self.session.start(
                self.command,
                cwd=self.cwd,
                env=self.environment,
                mcp_servers=servers,
                resume_session_id=resume,
                require_resume=persistent,
                session_metadata={
                    "claudeCode": {
                        "options": {
                            "model": self.choice.model,
                            "effort": self.choice.effort,
                            "permissionMode": self.choice.mode,
                            "persistSession": persistent,
                            "strictMcpConfig": True,
                            "allowDangerouslySkipPermissions": False,
                            # Internal live proof bounds; not product defaults.
                            "maxTurns": 4,
                            "maxBudgetUsd": 0.5,
                        }
                    }
                },
            )
            require_cleanup(self.session.capabilities)
            await self._verify_model()
        except (OSError, RuntimeError, TimeoutError, RequestError) as error:
            raise ApplicationError(
                "agent_resume_failed" if resume else "claude_start_failed",
                "Claude Code could not open the requested native session. Check its host "
                "login, configuration and development bridge. Nothing was replayed.",
                409,
            ) from error

    async def _verify_model(self) -> None:
        if self.session.connection is None or self.session.session_id is None:
            raise RuntimeError("Claude has no native session")
        response = await asyncio.wait_for(
            self.session.connection.ext_method(
                "flowfield/proofStatus", {"sessionId": self.session.session_id}
            ),
            10,
        )
        if (
            not isinstance(response, dict)
            or response.get("sessionId") != self.session.session_id
            or response.get("model") != self.choice.model
        ):
            raise ApplicationError(
                "agent_model_unconfirmed",
                "Claude Code did not confirm the exact requested native model. "
                "No prompt was sent. Refresh native choices before retrying.",
                409,
            )
        self.native_model = response["model"]

    async def configure(self, choice: AgentChoice) -> AgentChoice:
        if self.stopping:
            raise ApplicationError("agent_stopping", "The agent is stopping.", 409)
        self._configured = False
        if choice.harness != "claude-code" or choice.model != self.choice.model:
            raise ApplicationError(
                "agent_choice_unavailable",
                "This proof requires a fresh session for that model.",
                409,
            )
        try:
            if not choice.mode or choice.fast:
                raise ValueError("An explicit native mode is required; fast mode is unverified")
            await self.session.select("mode", choice.mode)
            await self.session.select("effort", choice.effort)
            expected = {"mode": choice.mode, "effort": choice.effort}
            if choice.fast is False and any(item["id"] == "fast" for item in self.session.config):
                await self.session.select("fast", "off")
                expected["fast"] = "off"
            current = {item["id"]: item.get("currentValue") for item in self.session.config}
            if any(current.get(key) != value for key, value in expected.items()):
                raise RuntimeError("Native settings changed while configuring")
            await self._verify_model()
            self.choice = choice.model_copy(deep=True)
            self._configured = True
            return self.choice.model_copy(deep=True)
        except (ValueError, RuntimeError, RequestError) as error:
            raise ApplicationError(
                "agent_choice_unavailable",
                "Claude Code could not apply the requested native effort and access mode.",
                409,
            ) from error

    async def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None,
        *,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if self.stopping:
            return {"status": "stopped"}
        if not self._configured:
            raise ApplicationError("agent_choice_unavailable", "Apply native settings first.", 409)
        await self._verify_model()
        return await super().prompt(text, on_permission, attachments=attachments)

    async def command_options(self) -> list[AgentCommand]:
        # Native command semantics require their own integrated proof. Do not offer
        # goal/account/plan commands merely because ACP advertised their names.
        return []
