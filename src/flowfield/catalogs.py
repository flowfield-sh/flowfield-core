"""Bounded project/native discovery with durable ownership and no automatic replay."""

import asyncio
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from flowfield.adapters.agent_selection import command_options, model_options
from flowfield.adapters.harness_host import status
from flowfield.agent_models import AgentChoice, AgentCommand
from flowfield.application import Workspace, now
from flowfield.errors import ApplicationError
from flowfield.execution_models import ModelOption
from flowfield.harness_models import CatalogOwnership, HarnessKind, HarnessRegistration
from flowfield.harness_settings import HarnessSettings


@dataclass
class CachedCatalog:
    models: list[ModelOption]
    at: float


class Catalogs:
    """One active discovery per concrete kind; at most eight cached project catalogs."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.jobs: dict[HarnessKind, tuple[str, asyncio.Task[list[ModelOption]]]] = {}
        self.cache: OrderedDict[str, CachedCatalog] = OrderedDict()
        self.closing = False

    def ownership(self, harness: HarnessKind) -> CatalogOwnership | None:
        with self.workspace.connection() as db:
            row = db.execute(
                "SELECT data FROM harness_catalogs WHERE harness=?", (harness,)
            ).fetchone()
            return CatalogOwnership.model_validate_json(row[0]) if row else None

    def restart(self) -> None:
        # Called only after the Supervisor has acquired exclusive service ownership.
        with self.workspace.connection(write=True) as db:
            for harness, raw in db.execute("SELECT harness,data FROM harness_catalogs").fetchall():
                record = CatalogOwnership.model_validate_json(raw)
                record.status = "uncertain"
                db.execute(
                    "UPDATE harness_catalogs SET data=? WHERE harness=?",
                    (record.model_dump_json(), harness),
                )

    def confirm_stopped(self, harness: HarnessKind, identity: str) -> None:
        live = self.jobs.get(harness)
        if live and not live[1].done():
            raise ApplicationError(
                "models_loading", "Discovery is still owned by the service.", 409
            )
        with self.workspace.connection(write=True) as db:
            row = db.execute(
                "SELECT data FROM harness_catalogs WHERE harness=?", (harness,)
            ).fetchone()
            record = CatalogOwnership.model_validate_json(row[0]) if row else None
            if record is None or record.id != identity or record.status != "uncertain":
                raise ApplicationError(
                    "stale_catalog_confirmation", "Reload harness status before confirming.", 409
                )
            db.execute("DELETE FROM harness_catalogs WHERE harness=?", (harness,))
        # Confirmation clears ownership, never accepts stale discovery results.
        self.cache.clear()

    async def run(
        self,
        registration: HarnessRegistration,
        *,
        project_id: str | None = None,
        project_path: str | None = None,
        refresh: bool = False,
    ) -> list[ModelOption]:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        self._current(registration)
        cwd = Path(self.workspace.project(project_id).path).resolve() if project_id else None
        if project_path is not None:
            if project_id is not None:
                raise ApplicationError(
                    "invalid_scope", "Choose a project ID or directory, not both."
                )
            cwd = self.workspace._setup_path(project_path)
        launch = status(self.workspace.directory, registration, os.environ).launch
        signature = repr((project_id, str(cwd), launch.model_dump_json()))
        kind = registration.harness
        live = self.jobs.get(kind)
        if live and not live[1].done():
            if live[0] != signature:
                raise ApplicationError(
                    "models_loading", "Another project or host configuration is being checked.", 409
                )
            models = await asyncio.shield(live[1])
            self._current(registration)
            return [item.model_copy(deep=True) for item in models]
        ownership = self.ownership(kind)
        if ownership and ownership.status == "running":
            raise ApplicationError(
                "models_loading", "Native discovery is still running. Try shortly.", 409
            )
        if ownership:
            raise ApplicationError(
                "catalog_cleanup_unconfirmed",
                "Native discovery cleanup is unconfirmed. "
                "Stop any remaining discovery on the host, then confirm it stopped in Harnesses.",
                409,
            )
        clock = asyncio.get_running_loop().time()
        cached = self.cache.get(signature)
        if cached and not refresh and clock - cached.at <= 300:
            self.cache.move_to_end(signature)
            return [item.model_copy(deep=True) for item in cached.models]
        self.cache.pop(signature, None)
        record = CatalogOwnership(
            id=uuid4().hex,
            harness=kind,
            project_id=project_id,
            status="running",
            started_at=now(),
            launch=launch,
        )
        # Persist before the task can execute its first native startup await.
        with self.workspace.connection(write=True) as db:
            if db.execute("SELECT 1 FROM harness_catalogs WHERE harness=?", (kind,)).fetchone():
                raise ApplicationError("models_loading", "Discovery already has an owner.", 409)
            db.execute(
                "INSERT INTO harness_catalogs VALUES (?,?)", (kind, record.model_dump_json())
            )
        job = asyncio.create_task(self._discover(record, registration, cwd, signature))
        job.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
        self.jobs[kind] = signature, job
        models = await asyncio.shield(job)
        self._current(registration)
        return [item.model_copy(deep=True) for item in models]

    def _current(self, registration: HarnessRegistration) -> None:
        if HarnessSettings(self.workspace).get(registration.harness) != registration:
            raise ApplicationError(
                "models_changed",
                "Host settings changed during discovery. Refresh native choices.",
                409,
            )

    def _finish(self, record: CatalogOwnership, cleaned: bool) -> None:
        with self.workspace.connection(write=True) as db:
            row = db.execute(
                "SELECT data FROM harness_catalogs WHERE harness=?", (record.harness,)
            ).fetchone()
            current = CatalogOwnership.model_validate_json(row[0]) if row else None
            if current and current.id == record.id:
                if cleaned:
                    db.execute("DELETE FROM harness_catalogs WHERE harness=?", (record.harness,))
                else:
                    record.status = "uncertain"
                    db.execute(
                        "UPDATE harness_catalogs SET data=? WHERE harness=?",
                        (record.model_dump_json(), record.harness),
                    )

    async def commands(
        self, registration: HarnessRegistration, project_id: str, choice: AgentChoice
    ) -> list[AgentCommand]:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        self._current(registration)
        cwd = Path(self.workspace.project(project_id).path).resolve()
        launch = status(self.workspace.directory, registration, os.environ).launch
        record = CatalogOwnership(
            id=uuid4().hex,
            harness=registration.harness,
            project_id=project_id,
            status="running",
            started_at=now(),
            launch=launch,
            operation="commands",
        )
        with self.workspace.connection(write=True) as db:
            row = db.execute(
                "SELECT data FROM harness_catalogs WHERE harness=?", (record.harness,)
            ).fetchone()
            if row:
                previous = CatalogOwnership.model_validate_json(row[0])
                raise ApplicationError(
                    "catalog_cleanup_unconfirmed"
                    if previous.status == "uncertain"
                    else "commands_loading",
                    "Native discovery already has an owner. Check harness status.",
                    409,
                )
            db.execute(
                "INSERT INTO harness_catalogs VALUES (?,?)",
                (record.harness, record.model_dump_json()),
            )
        cleaned = False

        def report(value: bool) -> None:
            nonlocal cleaned
            cleaned = value is True

        try:
            result = await command_options(
                self.workspace.directory, cwd, choice, registration=registration, on_cleanup=report
            )
            if not cleaned:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed",
                    "Native command discovery cleanup is unconfirmed.",
                    409,
                )
            self._current(registration)
            return result
        except Exception as error:
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError(
                "agent_commands_failed",
                "Native command discovery failed. Check the host harness configuration.",
                409,
            ) from error
        finally:
            self._finish(record, cleaned)

    async def _discover(
        self,
        record: CatalogOwnership,
        registration: HarnessRegistration,
        cwd: Path | None,
        signature: str,
    ) -> list[ModelOption]:
        cleaned = False

        def report(value: bool) -> None:
            nonlocal cleaned
            cleaned = value is True

        try:
            models = await model_options(
                self.workspace.directory, registration=registration, cwd=cwd, on_cleanup=report
            )
            if len(models) > 64:
                raise ApplicationError(
                    "agent_catalog_limit", "Native discovery returned too many models.", 409
                )
            if not cleaned:
                raise ApplicationError(
                    "agent_cleanup_unconfirmed", "Native catalog cleanup is unconfirmed.", 409
                )
            self._current(registration)
            self.cache[signature] = CachedCatalog(
                [item.model_copy(deep=True) for item in models], asyncio.get_running_loop().time()
            )
            self.cache.move_to_end(signature)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return models
        except Exception as error:
            if isinstance(error, ApplicationError):
                raise
            raise ApplicationError(
                "agent_catalog_failed",
                "Native model discovery failed. Check the host harness "
                "configuration and refresh its choices.",
                409,
            ) from error
        finally:
            self._finish(record, cleaned)

    async def close(self) -> None:
        self.closing = True
        jobs = [value[1] for value in self.jobs.values() if not value[1].done()]
        for job in jobs:
            if not job.cancelling():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
