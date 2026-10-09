"""Native Claude Code over ACP; exact identity and cleanup are checked before dispatch."""

import asyncio
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from acp.exceptions import RequestError
from acp.schema import HttpMcpServer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from flowfield.adapters import claude_install
from flowfield.adapters.acp_agent import AcpAgent, choices
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
from flowfield.execution_models import ModelOption, NativeMode
from flowfield.harness_models import HarnessRegistration

SESSION_INFO = {"version": 1, "method": "_flowfield/sessionInfo"}
MODES = {"default", "acceptEdits", "plan"}


class NativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=2000)
    resolvedModel: str | None = Field(max_length=200)


class NativeSessionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    sessionId: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    models: list[NativeModel] = Field(max_length=64)


def require_session_info(session: AcpSession) -> None:
    metadata = session.capabilities.get("_meta")
    capability = metadata.get("flowfield.sessionInfo") if isinstance(metadata, dict) else None
    if capability != SESSION_INFO or type(capability.get("version")) is not int:
        raise ApplicationError(
            "agent_metadata_unavailable", "Install the compatible Claude runtime.", 409
        )


async def session_info(session: AcpSession) -> NativeSessionInfo:
    require_session_info(session)
    if session.connection is None or session.session_id is None:
        raise RuntimeError("Claude has no native session")
    response = await asyncio.wait_for(
        session.connection.ext_method("flowfield/sessionInfo", {"sessionId": session.session_id}),
        10,
    )
    try:
        value = NativeSessionInfo.model_validate(response)
        if value.sessionId != session.session_id or type(response.get("version")) is not int:
            raise ValueError("Wrong native session or metadata version")
        return value
    except (ValidationError, ValueError) as error:
        raise ApplicationError(
            "agent_metadata_unavailable", "Claude returned incompatible native metadata.", 409
        ) from error


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
        choice: AgentChoice,
        bridge: Path | None = None,
        directory: Path | None = None,
        proof_limits: bool = False,
    ):
        if registration.harness != "claude-code" or choice.harness != "claude-code":
            raise ValueError("Claude requires a Claude registration and choice")
        if choice.mode not in MODES:
            raise ApplicationError("agent_mode_required", "Choose a native access mode.", 409)
        self._proof = bridge is not None
        self._limited = self._proof or proof_limits
        if bridge is None:
            if directory is None:
                raise ValueError("Installed Claude requires a Flowfield state directory")
            bridge = claude_install.installed(directory)
        if not bridge.is_absolute() or not bridge.is_file() or not os.access(bridge, os.X_OK):
            raise ApplicationError(
                "claude_bridge_unavailable", "Install or rebuild the requested Claude bridge.", 409
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
        self.launch.bridge_version = (
            "0.88.0-flowfield.proof.4" if self._proof else claude_install.VERSION
        )
        self.command = [str(bridge)] + (["--flowfield-proof-cleanup"] if self._proof else [])
        self.cwd = cwd
        self.choice = choice.model_copy(deep=True)
        self.native_model: str | None = None
        self._configured = False

    async def start(
        self, servers: list[HttpMcpServer], *, resume: str | None = None, persistent: bool = False
    ) -> None:
        self.cleanup_confirmed = False
        options: dict[str, str | bool | float | None] = {
            "model": self.choice.model,
            "permissionMode": self.choice.mode,
            "persistSession": persistent,
            "strictMcpConfig": True,
            "allowDangerouslySkipPermissions": False,
        }
        if self.choice.effort is not None:
            options["effort"] = self.choice.effort
        if self._limited:
            options.update(maxTurns=4, maxBudgetUsd=0.5)
        try:
            await self.session.start(
                self.command,
                cwd=self.cwd,
                env=self.environment,
                mcp_servers=servers,
                resume_session_id=resume,
                require_resume=persistent,
                session_metadata={"claudeCode": {"options": options}},
            )
            require_cleanup(self.session.capabilities)
            if not self._proof:
                require_session_info(self.session)
            await self._verify_model()
        except (OSError, RuntimeError, TimeoutError, RequestError) as error:
            raise ApplicationError(
                "agent_resume_failed" if resume else "claude_start_failed",
                "Claude Code could not open the requested native session. Check its host "
                "login, configuration and bridge. Nothing was replayed.",
                409,
            ) from error

    async def _verify_model(self) -> None:
        if not self._proof:
            info = await session_info(self.session)
            confirmed = info.model == self.choice.model and any(
                item.resolvedModel == self.choice.model for item in info.models
            )
            if not confirmed:
                raise ApplicationError(
                    "agent_model_unconfirmed",
                    "Claude did not confirm the requested model "
                    "in its native catalog. No prompt was sent. Refresh native choices.",
                    409,
                )
            self.native_model = info.model
            return
        if self.session.connection is None or self.session.session_id is None:
            raise RuntimeError("Claude has no native session")
        response = await asyncio.wait_for(
            self.session.connection.ext_method(
                "flowfield/proofStatus" if self._proof else "flowfield/sessionInfo",
                {"sessionId": self.session.session_id},
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
                "Claude requires a fresh session for that model.",
                409,
            )
        try:
            if choice.mode not in MODES or choice.fast:
                raise ValueError("An explicit native mode is required; fast mode is unverified")
            await self.session.select("mode", choice.mode)
            expected = {"mode": choice.mode}
            if choice.effort is not None:
                await self.session.select("effort", choice.effort)
                expected["effort"] = choice.effort
            elif any(item["value"] != "default" for item in choices(self.session.config, "effort")):
                raise ValueError("Choose an explicit effort for this model")
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


async def model_options(
    directory: Path,
    *,
    cwd: Path | None = None,
    registration: HarnessRegistration | None = None,
    on_cleanup: Callable[[bool], None] | None = None,
) -> list[ModelOption]:
    """Disposable model-free discovery; aliases resolve to exact policy-offered identities."""
    registration = registration or HarnessRegistration(harness="claude-code", revision=1)
    with tempfile.TemporaryDirectory(prefix="flowfield-claude-catalog-") as temporary:
        location = cwd or Path(temporary).resolve()
        if on_cleanup:
            on_cleanup(True)  # Resolution/verification starts no process.
        command, environment = claude_install.command(
            directory, os.environ, registration=registration
        )
        session = AcpSession(
            lambda _: None,
            cleanup=quiesce,
            shutdown_timeouts=CLAUDE_SHUTDOWN_TIMEOUTS,
        )
        if on_cleanup:
            on_cleanup(False)
        try:
            async with asyncio.timeout(120):
                await session.start(
                    command,
                    cwd=location,
                    env=environment,
                    mcp_servers=[],
                    session_metadata={
                        "claudeCode": {
                            "options": {
                                "persistSession": False,
                                "strictMcpConfig": True,
                                "permissionMode": "default",
                                "allowDangerouslySkipPermissions": False,
                            }
                        }
                    },
                )
                require_cleanup(session.capabilities)
                initial = await session_info(session)
                result: dict[str, ModelOption] = {}
                for model in initial.models:
                    # Unresolved aliases cannot become frozen application identities.
                    if not model.resolvedModel:
                        continue
                    await session.select("model", model.id)
                    applied = await session_info(session)
                    if applied.model != model.resolvedModel:
                        raise ApplicationError(
                            "agent_model_unconfirmed",
                            "Native model aliases changed during "
                            "discovery. Refresh native choices.",
                            409,
                        )
                    result[applied.model] = ModelOption(
                        id=applied.model,
                        name=model.name,
                        efforts=[
                            item["value"]
                            for item in choices(session.config, "effort")
                            if item["value"] != "default"
                        ],
                        modes=[
                            NativeMode(
                                id=item["value"],
                                name=item["name"],
                                description=item.get("description") or "",
                            )
                            for item in choices(session.config, "mode")
                            if item["value"] in MODES
                        ],
                        # Native Fast and commands require their own integrated proof.
                        fast=False,
                    )
                return list(result.values())
        finally:
            receipt = await session.close()
            if on_cleanup:
                on_cleanup(receipt.owned_work_stopped is True and receipt.process_group_exited)
            if receipt.owned_work_stopped is not True or not receipt.process_group_exited:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed",
                    "Claude catalog cleanup is unconfirmed. "
                    "Inspect the native host before starting more discovery.",
                    409,
                )
