"""Shared standalone MCP connection lifecycle; native configuration stays in adapters."""

import json
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from flowfield.errors import ApplicationError
from flowfield.harness_models import HarnessKind


class Connection(ABC):
    kind: HarnessKind
    label: str
    command: str

    def __init__(self, port: int, name: str = "flowfield"):
        self.url = f"http://127.0.0.1:{port}/mcp/"
        self.name = name
        executable = shutil.which(self.command)
        if executable is None:
            raise ApplicationError(
                f"{self.command}_missing",
                f"Install {self.label} and make {self.command} available on PATH, then retry.",
            )
        self.executable = executable

    def run(self, *args: str) -> str:
        # User-scoped configuration, unaffected by the invoking project's overrides.
        with tempfile.TemporaryDirectory(prefix="flowfield-connection-") as directory:
            try:
                result = subprocess.run(
                    [self.executable, *args],
                    cwd=directory,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ApplicationError(
                    f"{self.command}_unavailable",
                    f"{self.label} could not run. Check its installation and retry.",
                ) from error
        if result.returncode:
            # Native diagnostics can contain personal configuration; never echo them.
            raise ApplicationError(
                f"{self.command}_command_failed",
                f"{self.label} configuration command failed. Run {self.command} mcp list "
                "to diagnose; update the harness if its MCP commands are unavailable.",
            )
        return result.stdout

    @abstractmethod
    def configuration(self) -> dict[str, Any] | None: ...

    @abstractmethod
    def add(self) -> None: ...

    @abstractmethod
    def remove(self) -> None: ...

    @abstractmethod
    def matches(self, config: dict[str, Any]) -> bool: ...

    def read_json_configuration(self, path: Path) -> dict[str, Any] | None:
        """Read documented native JSON; writes always go through the native CLI."""
        try:
            data = json.loads(path.read_text())
            servers = data.get("mcpServers", {})
            if not isinstance(servers, dict):
                raise ValueError("Invalid server map")
            config = servers.get(self.name)
            if config is not None and not isinstance(config, dict):
                raise ValueError("Invalid server")
            return config
        except FileNotFoundError:
            return None
        except (OSError, ValueError, AttributeError) as error:
            raise ApplicationError(
                f"{self.command}_response_invalid",
                f"Cannot read {self.label} MCP configuration. "
                f"Run {self.command} mcp list to diagnose.",
            ) from error

    def conflict(self) -> ApplicationError:
        return ApplicationError(
            "connection_conflict",
            f"{self.label} already has a different server named {self.name}. "
            f"Use --name with another name, or inspect {self.command} mcp list. "
            "Existing settings were preserved.",
        )

    def connect(self) -> dict[str, Any]:
        config = self.configuration()
        if config is not None and not self.matches(config):
            raise self.conflict()
        # Prove the intended service is usable before changing harness configuration.
        tools = anyio.run(probe, self.url)
        changed = config is None
        if changed:
            self.add()
        config = self.configuration()
        if config is None or not self.matches(config):
            raise ApplicationError(
                "connection_not_saved",
                f"{self.label} did not retain the requested connection. "
                f"Inspect {self.command} mcp list and retry.",
            )
        return self.report(config, tools, changed=changed)

    def report(
        self, config: dict[str, Any], tools: list[str], *, changed: bool = False
    ) -> dict[str, Any]:
        enabled = config.get("enabled")
        return {
            "harness": self.kind,
            "name": self.name,
            "url": self.url,
            "configured": True,
            "enabled": enabled,
            "service": "reachable",
            "tools": tools,
            "changed": changed,
            "client": "not_observed",
            "message": (
                f"Connection saved. Restart {self.label}, then check /mcp."
                if changed
                else f"Connection is configured but disabled in {self.label}. "
                "Enable it in native settings."
                if enabled is False
                else "Connection is configured and the service is ready. "
                f"If this client has not loaded it, restart {self.label} and check /mcp."
            ),
        }

    def doctor(self) -> dict[str, Any]:
        config = self.configuration()
        if config is None:
            raise ApplicationError(
                "not_connected",
                f"No connection configured. Run flowfield integration connect {self.kind} "
                "with the same --port and --name.",
            )
        if not self.matches(config):
            raise self.conflict()
        return self.report(config, anyio.run(probe, self.url))

    def disconnect(self) -> dict[str, Any]:
        config = self.configuration()
        if config is not None:
            if not self.matches(config):
                raise self.conflict()
            self.remove()
            if self.configuration() is not None:
                raise ApplicationError(
                    "connection_not_removed",
                    f"{self.label} still reports this connection. Inspect {self.command} mcp list.",
                )
        return {
            "harness": self.kind,
            "name": self.name,
            "configured": False,
            "changed": config is not None,
            "message": f"Connection removed. Restart {self.label} to unload its tools. "
            "Projects and tasks are retained."
            if config
            else "Connection is already absent. Projects and tasks are retained.",
        }


async def probe(url: str) -> list[str]:
    try:
        with anyio.fail_after(10):
            async with (
                httpx.AsyncClient(trust_env=False, follow_redirects=False) as http,
                streamable_http_client(url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                initialized = await session.initialize()
                if initialized.serverInfo.name != "Flowfield":
                    raise ValueError("Wrong server")
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                if not {
                    "list_projects",
                    "get_task",
                    "create_task",
                    "edit_task",
                    "get_board",
                    "prioritize_task",
                    "review_result",
                }.issubset(tools):
                    raise ValueError("Missing Flowfield tools")
                return tools
    except Exception as error:
        raise ApplicationError(
            "mcp_unavailable",
            f"Cannot verify Flowfield MCP at {url}. "
            "Start flowfield serve on this port, then retry. No model call is needed.",
        ) from error
