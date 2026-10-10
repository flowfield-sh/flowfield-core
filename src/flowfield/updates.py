"""Bounded PyPI discovery and a shared durable cache; never installs software."""

import asyncio
import json
import os
import platform
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from flowfield import __version__
from flowfield.application import Workspace
from flowfield.notifications import (
    NoticeAction,
    NoticeCommand,
    NoticeContent,
    publish,
    save_state,
    state,
)
from flowfield.storage import DATABASE, acquire_lock, connect, require_supported, version

INDEX_URL = "https://pypi.org/simple/flowfield-core/"
RELEASE_NOTES = "https://github.com/flowfield-sh/flowfield-core/releases"
INTERVAL = 3600
MAX_RESPONSE = 2_000_000


def enabled_by_environment() -> bool:
    return os.environ.get("FLOWFIELD_UPDATE_CHECKS", "1").lower() not in {"0", "false", "off"}


def available(latest: str | None, installed: str) -> str | None:
    if latest is None:
        return None
    candidate, current = Version(latest), Version(installed)
    if candidate > current and (current.is_prerelease or not candidate.is_prerelease):
        return latest
    return None


def instructions(latest: str) -> list[NoticeCommand]:
    prerelease = Version(latest).is_prerelease
    return [
        NoticeCommand(
            label="uv",
            command="uv tool upgrade flowfield-core"
            + (" --prerelease allow" if prerelease else ""),
        ),
        NoticeCommand(
            label="pip (in your Flowfield environment)",
            command="python -m pip install --upgrade flowfield-core"
            + (" --pre" if prerelease else ""),
        ),
    ]


class UpdateStatus(BaseModel):
    installed_version: str
    available_version: str | None = None
    latest_version: str | None = None
    automatic: bool = True
    disabled_by_environment: bool = False
    checking: bool = False
    last_attempt: str | None = None
    last_success: str | None = None
    error: str | None = None
    cached: bool = False
    release_notes: str = RELEASE_NOTES
    commands: list[NoticeCommand] = []


def status_from(data: dict[str, Any], installed: str, *, checking: bool = False) -> UpdateStatus:
    latest = data.get("latest_version")
    newer = available(latest, installed)
    error = data.get("error")
    if not checking and not error and data.get("last_attempt", "") > data.get("last_success", ""):
        error = "The last update check did not complete. Try again."
    return UpdateStatus(
        installed_version=installed,
        available_version=newer,
        latest_version=latest,
        automatic=data.get("automatic", True) and enabled_by_environment(),
        disabled_by_environment=not enabled_by_environment(),
        checking=checking,
        last_attempt=data.get("last_attempt"),
        last_success=data.get("last_success"),
        error=error,
        commands=instructions(newer) if newer else [],
    )


def cached_status(directory: Path) -> UpdateStatus:
    """Offline inspection only; do not create state or migrate an older database."""
    data: dict[str, Any] = {}
    if (directory / DATABASE).exists():
        with (
            acquire_lock(directory, ".initialize.lock", shared=True),
            closing(connect(directory / DATABASE, readonly=True)) as db,
        ):
            require_supported(version(db))
            if version(db) >= 31:
                data = state(db, "updates")
    return status_from(data, __version__).model_copy(update={"cached": True})


def latest_release(payload: Any, installed: str, python: str) -> str | None:
    if (
        not isinstance(payload, dict)
        or canonicalize_name(str(payload.get("name", ""))) != "flowfield-core"
    ):
        raise ValueError("Unexpected package index response")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) > 10_000:
        raise ValueError("Invalid package file list")
    candidates: set[Version] = set()
    supported_tags = set(sys_tags())
    current = Version(installed)
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
            raise ValueError("Invalid package file metadata")
        if file.get("yanked", False) is not False:
            continue
        filename = file["filename"]
        try:
            if filename.endswith(".whl"):
                name, candidate, _, tags = parse_wheel_filename(filename)
                if not supported_tags.intersection(tags):
                    continue
            else:
                name, candidate = parse_sdist_filename(filename)
            if name != "flowfield-core" or candidate.local:
                continue
            requires = file.get("requires-python")
            if requires and not SpecifierSet(requires).contains(python, prereleases=True):
                continue
        except (InvalidSdistFilename, InvalidWheelFilename, InvalidSpecifier, TypeError) as error:
            raise ValueError("Invalid package version metadata") from error
        if current.is_prerelease or not candidate.is_prerelease:
            candidates.add(candidate)
    return str(max(candidates)) if candidates else None


class Updates:
    def __init__(
        self,
        workspace: Workspace,
        *,
        installed: str = __version__,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.workspace = workspace
        self.installed = installed
        self.transport = transport
        self.clock = clock
        self.task: asyncio.Task[None] | None = None
        self.first_due = clock().timestamp() + INTERVAL

    def status(self) -> UpdateStatus:
        with self.workspace.connection() as db:
            return status_from(
                state(db, "updates"),
                self.installed,
                checking=self.task is not None and not self.task.done(),
            )

    def configure(self, automatic: bool) -> UpdateStatus:
        with self.workspace.connection(write=True, notify=False) as db:
            data = state(db, "updates")
            data["automatic"] = automatic
            save_state(db, "updates", data)
        return self.status()

    def reconcile(self) -> None:
        with self.workspace.connection(write=True, notify=False) as db:
            self._reconcile(db, state(db, "updates"))

    def _reconcile(self, db: sqlite3.Connection, data: dict[str, Any]) -> None:
        newer = available(data.get("latest_version"), self.installed)
        key = f"update:{newer}" if newer else ""
        db.execute(
            "UPDATE notifications SET resolved_at=? WHERE source='update' "
            "AND key<>? AND resolved_at IS NULL",
            (self.clock().isoformat(), key),
        )
        if newer and newer != data.get("announced_version"):
            publish(
                db,
                "update",
                key,
                NoticeContent(
                    title=f"Flowfield {newer} is available",
                    message="Stop Flowfield, upgrade with your installation method, then restart. "
                    "Supported database migrations run automatically; "
                    "each project keeps its saved queue setting.",
                    actions=[NoticeAction(label="Release notes", href=RELEASE_NOTES)],
                    commands=instructions(newer),
                ),
            )
            data["announced_version"] = newer
            save_state(db, "updates", data)

    def trigger(self, reason: str = "manual") -> UpdateStatus:
        current = self.status()
        if current.checking or (reason != "manual" and not current.automatic):
            return current
        last = (
            datetime.fromisoformat(current.last_attempt).timestamp() if current.last_attempt else 0
        )
        interval = INTERVAL if reason == "hourly" else 30 if reason == "startup" else 5
        if self.clock().timestamp() < (
            last + interval if last else self.first_due if reason == "hourly" else 0
        ):
            return current
        self.task = asyncio.create_task(self._check())
        return self.status()

    async def _check(self) -> None:
        attempted = self.clock().isoformat()
        with self.workspace.connection(write=True, notify=False) as db:
            data = state(db, "updates")
            data.update(last_attempt=attempted, error=None)
            save_state(db, "updates", data)
        try:
            async with (
                asyncio.timeout(6),
                httpx.AsyncClient(
                    timeout=3, follow_redirects=False, trust_env=False, transport=self.transport
                ) as client,
            ):
                async with client.stream(
                    "GET",
                    INDEX_URL,
                    headers={
                        "Accept": "application/vnd.pypi.simple.v1+json",
                        "User-Agent": "flowfield-update-check",
                    },
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise ValueError("Package index response exceeds the size limit")
                    latest = latest_release(
                        json.loads(body), self.installed, platform.python_version()
                    )
        except (httpx.HTTPError, TimeoutError, ValueError, InvalidVersion) as error:
            problem = "Could not check PyPI. Try again when connected."
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
                problem = "flowfield-core has no published PyPI release yet."
            elif isinstance(error, ValueError):
                problem = "PyPI returned an invalid or oversized release response."
            with self.workspace.connection(write=True, notify=False) as db:
                data = state(db, "updates")
                data["error"] = problem
                save_state(db, "updates", data)
        else:
            with self.workspace.connection(write=True, notify=False) as db:
                data = state(db, "updates")
                data.update(
                    latest_version=latest, last_success=self.clock().isoformat(), error=None
                )
                save_state(db, "updates", data)
                self._reconcile(db, data)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
