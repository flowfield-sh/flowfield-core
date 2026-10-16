"""Shared native review lifecycle; purpose owners supply frozen context and scoped tools."""

import asyncio
import json
import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

from flowfield.adapters.agent_contract import PermissionRequest
from flowfield.adapters.agent_mcp import serve_scope
from flowfield.adapters.agent_selection import create
from flowfield.adapters.git_workspace import git
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import EffectiveAgent
from flowfield.agent_tools import ScopedTools
from flowfield.application import now
from flowfield.errors import ApplicationError
from flowfield.run_activity import ActivityRecorder, ActivityUpdate

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

ReviewStatus = Literal[
    "queued",
    "preparing",
    "running",
    "waiting",
    "decided",
    "stale",
    "stopped",
    "uncertain",
    "failed",
]


class ReviewJob(Protocol):
    id: str
    project_id: str
    revision: int
    status: ReviewStatus
    attempt_id: str | None
    problem: str | None
    completed_at: str | None


J = TypeVar("J", bound=ReviewJob)


class ReviewOwner(Protocol[J]):
    attempt_table: str

    def get(self, project: str, identity: str) -> J: ...
    def _get(self, db: sqlite3.Connection, project: str, identity: str) -> J: ...
    def _save(self, db: sqlite3.Connection, value: J) -> None: ...
    def begin_attempt(self, project: str, identity: str) -> J: ...
    def claim(self, project: str, identity: str, expected_revision: int) -> J: ...
    def started(self, project: str, identity: str, expected_revision: int) -> J: ...
    def attempt(self, identity: str) -> dict[str, Any]: ...
    def save_attempt(self, identity: str, updates: dict[str, Any]) -> None: ...
    def write(self, project: str, attempt: str, updates: list[ActivityUpdate]) -> None: ...
    def finish_attempt(
        self,
        project: str,
        identity: str,
        attempt: str,
        *,
        cleanup_confirmed: bool,
        problem: str | None = None,
    ) -> J: ...
    def stop(
        self, project: str, identity: str, expected_revision: int, *, cleanup_confirmed: bool
    ) -> J: ...


@dataclass
class ReviewLaunch:
    candidate_commit: str
    agent: EffectiveAgent
    scope: ScopedTools
    brief: dict[str, Any]


class NativeReviewExecution[J: ReviewJob](ABC):
    def __init__(self, supervisor: "Supervisor", reviews: ReviewOwner[J]):
        self.supervisor, self.reviews = supervisor, reviews

    @abstractmethod
    def launch(self, review: J, attempt: str) -> ReviewLaunch:
        raise NotImplementedError

    async def start_queued(self, project: str, identity: str) -> None:
        service = self.supervisor
        if service.closing:
            return
        current = self.reviews.get(project, identity)
        claiming = asyncio.create_task(
            asyncio.to_thread(self.reviews.claim, project, identity, current.revision)
        )
        try:
            review = await asyncio.shield(claiming)
        except asyncio.CancelledError:
            while not claiming.done():
                try:
                    await asyncio.shield(claiming)
                except asyncio.CancelledError:
                    pass
            if not claiming.cancelled() and claiming.exception() is None:
                claimed = claiming.result()
                if claimed.status == "preparing":
                    self.reviews.stop(project, identity, claimed.revision, cleanup_confirmed=True)
            raise
        if review.status != "preparing":
            return
        task = asyncio.create_task(self.execute(review))
        service.jobs[review.id] = task
        task.add_done_callback(partial(self._finished, project, review.id))

    def _finished(self, project: str, identity: str, task: asyncio.Task[None]) -> None:
        self.supervisor.jobs.pop(identity, None)
        if task.cancelled() or task.exception():
            with self.supervisor.workspace.connection(write=True, project_id=project) as db:
                review = self.reviews._get(db, project, identity)
                if review.status in ("preparing", "running"):
                    review.status, review.problem = (
                        "uncertain",
                        (
                            "Review execution ended unexpectedly. Inspect its retained process "
                            "evidence and confirm cleanup before retrying."
                        ),
                    )
                    self.reviews._save(db, review)

    async def execute(self, review: J) -> None:
        service = self.supervisor
        client = None
        environment = None
        problem = None
        confirmed = True
        activity: ActivityRecorder | None = None
        scope_stack = AsyncExitStack()
        review = self.reviews.begin_attempt(review.project_id, review.id)
        assert review.attempt_id
        attempt = review.attempt_id

        def save(**values: Any) -> None:
            self.reviews.save_attempt(attempt, values)

        async def permission(request: PermissionRequest) -> str | None:
            # No background permission escalation. Ask the human through a scoped question.
            # Native requests remain harness-owned; only the bound Flowfield tool is allowed.
            managed = request.managed_tool
            allowed = bool(
                managed and managed.server == server.name and managed.name in scope.tools
            )
            kind = "allow_once" if allowed else "reject_once"
            return next(
                (identity for identity, _, option in request.options if option == kind), None
            )

        try:
            launch = self.launch(review, attempt)
            project = service.workspace.project(review.project_id)
            preparing = asyncio.create_task(
                asyncio.to_thread(
                    LocalHost(os.environ).prepare,
                    service.workspace.directory,
                    Path(project.path),
                    attempt,
                    launch.candidate_commit,
                )
            )
            try:
                environment = await asyncio.shield(preparing)
            except asyncio.CancelledError:
                # Preparation owns Git writes until its thread is finished.
                while not preparing.done():
                    try:
                        await asyncio.shield(preparing)
                    except asyncio.CancelledError:
                        pass
                environment = preparing.result()
                save(checkout=str(environment.checkout), runtime=str(environment.runtime))
                raise
            frozen = launch.agent
            choice = frozen.choice
            client = create(
                choice,
                service.workspace.directory,
                environment.checkout,
                environment.launch_environment(),
                registration=frozen.registration,
            )
            service.clients[review.id] = client
            scope = launch.scope
            server = await scope_stack.enter_async_context(serve_scope(scope))
            save(
                checkout=str(environment.checkout),
                runtime=str(environment.runtime),
                native_launch_started=True,
                cleanup_confirmed=False,
                requested_agent=choice.model_dump(),
                harness_launch=client.launch.model_dump() if client.launch else None,
            )
            confirmed = False
            await client.start([server])
            from flowfield.supervisor import process_stamp

            assert client.process
            save(
                pid=client.process.pid,
                process_stamp=await asyncio.to_thread(process_stamp, client.process.pid),
            )
            applied = await client.configure(choice)
            save(
                applied_agent=applied.model_dump(),
                session_id=client.session_id,
                status="running",
            )
            current = self.reviews.get(review.project_id, review.id)
            if current.status != "preparing" or service.closing:
                raise ApplicationError(
                    "review_stopped", "Review stopped before its model turn.", 409
                )
            self.reviews.started(review.project_id, review.id, current.revision)
            activity = ActivityRecorder(
                service.workspace, review.project_id, attempt, store=self.reviews
            )
            client.on_activity = activity.emit
            brief = {**launch.brief, "flowfield_connection": server.name}
            outcome = await client.prompt(json.dumps(brief, ensure_ascii=False), permission)
            if outcome.get("status") != "completed":
                problem = "Review turn did not complete. Evidence is retained; retry explicitly."
            scope.revoke()
            confirmed = await client.stop()
            if environment and confirmed:
                # Clarification and assessment cannot quietly become an implementation candidate.
                dirty = await asyncio.to_thread(git, environment.checkout, "status", "--porcelain")
                head = await asyncio.to_thread(git, environment.checkout, "rev-parse", "HEAD")
                if dirty or head.decode().strip() != launch.candidate_commit:
                    problem = (
                        "Review changed its inspection checkout. No conclusion was applied; "
                        "inspect retained work."
                    )
        except asyncio.CancelledError:
            problem = "Review stopped; retained evidence is preserved."
        except Exception as error:
            problem = (
                error.message
                if isinstance(error, ApplicationError)
                else (
                    f"Review preparation/execution failed ({type(error).__name__}); "
                    "inspect retained evidence."
                )
            )
        finally:
            if client:
                try:
                    confirmed = await client.stop()
                    await client.close()
                    confirmed = confirmed and client.cleanup_confirmed
                except BaseException:
                    confirmed = False
            try:
                await scope_stack.aclose()
            except BaseException:
                confirmed = False
            save(cleanup_confirmed=confirmed, completed_at=now())
            if activity:
                await activity.close()
            service.clients.pop(review.id, None)
            self.reviews.finish_attempt(
                review.project_id, review.id, attempt, cleanup_confirmed=confirmed, problem=problem
            )

    async def stop(self, project: str, identity: str, expected_revision: int) -> J:
        service = self.supervisor
        current = self.reviews.get(project, identity)
        service.workspace._current(current.revision, expected_revision)
        client = service.clients.get(identity)
        job = service.jobs.get(identity)
        # Revoke ownership before awaiting native cancellation; a late verdict cannot win.
        self.reviews.stop(
            project,
            identity,
            expected_revision,
            cleanup_confirmed=not (client or job) and current.status != "uncertain",
        )
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        if client:
            confirmed = await client.stop()
        elif current.attempt_id:
            data = self.reviews.attempt(current.attempt_id)
            confirmed = data.get("cleanup_confirmed") is True
        else:
            # Every launch first creates an attempt receipt. No receipt means no native launch.
            confirmed = True
        finished = self.reviews.get(project, identity)
        if (
            finished.status == "uncertain"
            and confirmed
            and finished.attempt_id == current.attempt_id
        ):
            finished = self.reviews.stop(
                project, identity, finished.revision, cleanup_confirmed=True
            )
        return finished

    def confirm_stopped(self, project: str, identity: str, expected_revision: int) -> J:
        if identity in self.supervisor.jobs or identity in self.supervisor.clients:
            raise ApplicationError("review_active", "Stop the live review first.", 409)
        with self.supervisor.workspace.connection(write=True, project_id=project) as db:
            review = self.reviews._get(db, project, identity)
            self.supervisor.workspace._current(review.revision, expected_revision)
            if review.status != "uncertain":
                raise ApplicationError(
                    "review_not_uncertain", "This review does not need cleanup confirmation.", 409
                )
            review.status, review.problem, review.completed_at = (
                "stopped",
                "You confirmed the review and its commands stopped. Retry explicitly.",
                now(),
            )
            if review.attempt_id:
                data = self.reviews.attempt(review.attempt_id)
                data.update(
                    cleanup_confirmed=True, cleanup_confirmed_by="human", cleanup_confirmed_at=now()
                )
                db.execute(
                    f"UPDATE {self.reviews.attempt_table} SET data=? WHERE id=?",
                    (json.dumps(data), review.attempt_id),
                )
            self.reviews._save(db, review)
            return review
