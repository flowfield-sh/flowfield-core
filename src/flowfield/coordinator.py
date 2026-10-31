"""Explicit coordinator turns; independent of worker scheduling and capacity."""

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from flowfield.adapters.agent_contract import Agent
from flowfield.adapters.agent_mcp import serve_scope
from flowfield.adapters.agent_permissions import permission_handler
from flowfield.adapters.agent_selection import create
from flowfield.adapters.coordinator_directory import for_scope as coordinator_directory
from flowfield.adapters.coordinator_directory import repositories_for_scope
from flowfield.adapters.harness_host import resolve
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentCommand
from flowfield.agent_tools import workspace_scope
from flowfield.attachments import Attachments
from flowfield.context_measurements import ContextMeasurements
from flowfield.coordinator_models import CoordinatorSend, CoordinatorTurn
from flowfield.coordinator_settings import CoordinatorSettings
from flowfield.coordinator_store import CoordinatorStore
from flowfield.documents import Documents
from flowfield.errors import ApplicationError
from flowfield.guidance import template
from flowfield.harness_settings import HarnessSettings
from flowfield.reads import excerpt
from flowfield.run_activity import ActivityRecorder, ActivityUpdate
from flowfield.usage import Usage

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

# The shared role guide is retrieved by workflow; universal rules stay in every prompt.
GUIDANCE = (
    template("mcp-instructions.md").strip()
    + "\nBefore acting in a workflow, use get_coordinator_guide(mode=outline), "
    "then read the relevant section in full. Detailed workflow guidance is "
    "required for that workflow, not optional. Retrieve again if context is "
    "lost.\n"
    + """

Built-in conversation context:
Use only the named scoped MCP connection for Flowfield operations, never ambient connections,
CLI or private service/database files. The working directory is Flowfield-managed, not a project
or a source of workspace identity. Read project paths through MCP for repository operations.
Use host tools under the selected native access mode; preserve existing files and dirty work.
Native session history carries this conversation. recent_conversation is a one-time handoff;
its source boundary was frozen when this message was accepted. When history_mode is retrieve
and source_count is nonzero, read get_coordinator_history before source_before before continuing,
even if recent_conversation is empty. Follow text_sources/older_history as needed.
Excerpted text is retrievable; output_omitted identifies unsaved text that cannot be recovered.
Earlier attachment identities carry no contents or permissions; ask for reattachment when needed.
"""
)


class Coordinator:
    def __init__(self, supervisor: "Supervisor"):
        self.supervisor = supervisor
        self.store = CoordinatorStore(supervisor.workspace)
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.finishing: set[str] = set()
        self.closing = False
        self.command_jobs: dict[str, tuple[float, str, asyncio.Task[list[AgentCommand]]]] = {}

    async def commands(self, project: str, *, refresh: bool = False) -> list[AgentCommand]:
        if self.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        workspace = self.supervisor.workspace
        settings = CoordinatorSettings(workspace).get(project)
        if not settings.effective:
            raise ApplicationError(
                "coordinator_settings", "Choose coordinator settings first.", 409
            )
        cwd = str(coordinator_directory(workspace, project))
        choice = settings.effective.choice
        registration = HarnessSettings(workspace).get(choice.harness)
        signature = (
            cwd + choice.model_dump_json() + resolve(registration, os.environ).model_dump_json()
        )
        clock = asyncio.get_running_loop().time()
        cached = self.command_jobs.get(project)
        if cached and not cached[2].done():
            if cached[1] != signature:
                raise ApplicationError(
                    "commands_loading", "Settings changed; reload commands shortly.", 409
                )
            return await asyncio.shield(cached[2])
        if cached and not refresh and clock - cached[0] < 300 and cached[1] == signature:
            if not cached[2].cancelled() and cached[2].exception() is None:
                return cached[2].result()
        if sum(not job.done() for _, _, job in self.command_jobs.values()) >= 4:
            raise ApplicationError(
                "commands_busy", "Command discovery is busy. Try again shortly.", 409
            )
        for key in list(self.command_jobs):
            if len(self.command_jobs) < 64:
                break
            if self.command_jobs[key][2].done():
                del self.command_jobs[key]
        job = asyncio.create_task(self.supervisor.catalogs.commands(registration, project, choice))
        self.command_jobs[project] = (clock, signature, job)
        return await asyncio.shield(job)

    def send(self, project: str, conversation: str, request: CoordinatorSend) -> CoordinatorTurn:
        if self.closing or self.supervisor.closing:
            raise ApplicationError("service_stopping", "The service is stopping.", 409)
        turn, created = self.store.reserve(
            project, conversation, request, available=len(self.jobs) < 16
        )
        if created:
            job = asyncio.create_task(self._run(turn))
            self.jobs[turn.id] = job
            job.add_done_callback(lambda _: self.jobs.pop(turn.id, None))
            job.add_done_callback(lambda _: self.finishing.discard(turn.id))
        return turn

    def _prompt(
        self,
        turn: CoordinatorTurn,
        server: str,
        *,
        resumed: bool = False,
        budget: int | None = None,
        record_handoff: bool = False,
    ) -> str:
        instructions = {
            "instructions": f"Use the {server} MCP connection.\n" + GUIDANCE,
            "workspace_id": turn.workspace_id,
            "flowfield_connection": server,
            "human_message": turn.text,
            "adjacent_view": turn.view_context.model_dump() if turn.view_context else None,
            "selected_task": turn.task_context.model_dump() if turn.task_context else None,
        }
        if turn.guidance_revision is not None:
            instructions["workspace_guidance"] = excerpt(
                Documents(self.supervisor.workspace)
                .get(turn.workspace_id, "guidance", turn.guidance_revision)
                .model_dump(),
                6000,
            )
        if resumed:
            return json.dumps(instructions, ensure_ascii=False)
        return json.dumps(
            {
                **instructions,
                **self.store.handoff(
                    turn.workspace_id,
                    turn.id,
                    record=record_handoff,
                    budget=None
                    if budget is None
                    else max(
                        0,
                        budget - len(json.dumps(instructions, ensure_ascii=False).encode("utf-8")),
                    ),
                ),
            },
            ensure_ascii=False,
        )

    async def _run(self, turn: CoordinatorTurn) -> None:
        workspace = self.supervisor.workspace
        client: Agent | None = None
        temporary: tempfile.TemporaryDirectory[str] | None = None
        recorder = ActivityRecorder(
            workspace, turn.workspace_id, turn.id, store=self.store, preserve_prose=True
        )
        status: Literal["completed", "failed", "stopped"] = "failed"
        notice = ""
        generation_id: str | None = None
        startup_pending = False

        def live_scope() -> None:
            if generation_id is None:
                raise ApplicationError("scope_closed", "The coordinator session is not ready.", 403)
            with workspace.connection() as db:
                self.store.sessions.actor(
                    db,
                    turn.workspace_id,
                    turn.id,
                    generation_id,
                    statuses=("starting", "running"),
                )

        try:
            grant = await workspace_scope(
                self.supervisor,
                turn.workspace_id,
                author=f"coordinator:{turn.id}",
                check=live_scope,
            )
            measurements = ContextMeasurements(
                workspace, turn.workspace_id, "coordinator_turn", turn.id
            )
            grant.measurements = measurements
            # The same name replaces the previous endpoint on resume; credentials
            # and grants remain fresh and are revoked at the end of every turn.
            async with serve_scope(grant, name="flowfield_" + turn.conversation_id) as server:
                try:
                    cwd = coordinator_directory(workspace, turn.workspace_id)
                    temporary = tempfile.TemporaryDirectory(prefix="flowfield-coordinator-")
                    environment = LocalHost(os.environ).launch_environment(
                        cwd, Path(temporary.name)
                    )
                    repositories = repositories_for_scope(workspace, turn.workspace_id)
                    client = create(
                        turn.settings.choice,
                        workspace.directory,
                        cwd,
                        environment,
                        registration=turn.settings.registration,
                        project_directories=repositories,
                    )
                    with workspace.connection(write=True) as db:
                        current = self.store._get(db, turn.workspace_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        generation, resumed = self.store.sessions.prepare(
                            db,
                            turn.workspace_id,
                            turn.id,
                            turn.settings.choice,
                            str(cwd),
                            client.launch,
                            project_directories=tuple(str(path) for path in repositories),
                        )
                        generation_id = generation.id
                        session_id = generation.session_id if resumed else None
                        recorder.store = self.store.activity_store(generation_id)
                        current.native_started = True
                        current.launch = client.launch
                        self.store._save(db, current)
                    if not session_id:
                        startup_pending = True
                        recorder.emit(
                            ActivityUpdate(
                                key="session",
                                kind="status",
                                text="Starting a fresh agent session…",
                            )
                        )
                    Usage(workspace).bind(turn.workspace_id, "coordinator_turn", turn.id, client)
                    client.on_context = measurements.native
                    await client.start([server], resume=session_id, persistent=True)
                    applied = await client.configure(turn.settings.choice)
                    assert client.session_id
                    self.store.sessions.created(
                        turn.workspace_id,
                        turn.id,
                        generation_id,
                        client.session_id,
                        launch=client.launch,
                    )
                    attachments = Attachments(workspace).inputs(
                        turn.workspace_id,
                        None,
                        turn.text,
                    )
                    # Reject unsupported input before promoting the current binding:
                    # native history is only materialized by its first prompt.
                    client.validate_attachments(attachments)
                    native_command = await client.command_prompt(
                        turn.text, has_history=session_id is not None, attachments=attachments
                    )
                    assert client.session_id
                    with workspace.connection(write=True) as db:
                        current = self.store._get(db, turn.workspace_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        self.store.sessions.dispatch(
                            db,
                            turn.workspace_id,
                            turn.id,
                            generation_id,
                            turn.settings.choice,
                            command=bool(native_command),
                        )
                        current.status = "running"
                        current.session = "resumed" if session_id else "new"
                        current.applied = turn.settings.model_copy(update={"choice": applied})
                        self.store._save(db, current)
                    # UTF-8 bytes conservatively bound tokens without guessing a model's
                    # tokenizer. Reserve the current input, including attachments, first.
                    budget = client.input_tokens_available
                    if budget is not None:
                        budget = min(budget, client.max_prompt_bytes or budget)
                        budget = max(0, budget - len(json.dumps(attachments).encode("utf-8")))
                    prompt = native_command or self._prompt(
                        turn,
                        server.name,
                        resumed=session_id is not None,
                        budget=budget,
                        record_handoff=True,
                    )
                    handoff = (
                        [] if native_command else json.loads(prompt).get("recent_conversation", [])
                    )
                    session_note = (
                        "Agent session resumed with native conversation history."
                        if session_id
                        else "Checking the harness without starting a conversation."
                        if native_command
                        else "New agent session started."
                    )
                    if handoff:
                        session_note += (
                            f" Included the latest {len(handoff)} saved exchanges; "
                            "older history stays available. Reattach earlier files when needed."
                        )
                    elif (
                        not session_id
                        and not native_command
                        and json.loads(prompt).get("source_count")
                    ):
                        session_note += (
                            " Saved history is available through Flowfield tools; "
                            "reattach earlier files when needed."
                        )
                    if not session_id or native_command:
                        recorder.emit(
                            ActivityUpdate(key="session", kind="status", text=session_note)
                        )
                    startup_pending = False
                    client.on_activity = recorder.emit
                    assert client.session_id
                    async with self.supervisor.permissions.turn(
                        turn.workspace_id,
                        "coordinator",
                        session_id=client.session_id,
                        turn_id=turn.id,
                        conversation_id=turn.conversation_id,
                        generation_id=generation_id,
                    ) as permission_turn:
                        Usage(workspace).start(
                            turn.workspace_id,
                            "coordinator_turn",
                            turn.id,
                            client.session_id,
                            purpose="command" if native_command else "message",
                        )
                        outcome = await client.prompt(
                            measurements.prompt(
                                prompt, resumed=session_id is not None, attachments=len(attachments)
                            ),
                            permission_handler(permission_turn),
                            attachments=attachments,
                        )
                    status = "completed" if outcome.get("status") == "completed" else "failed"
                    if status == "failed":
                        notice = (
                            "The agent ended before completing its reply. "
                            "Send a new message to continue."
                        )
                finally:
                    # Fence new task mutations before stopping native work. The server drains
                    # already-running canonical operations; cancellation never rolls them back.
                    grant.revoke()
                    self.finishing.add(turn.id)
                    with workspace.connection(write=True) as db:
                        if self.store.owns(db, turn.workspace_id, turn.id, generation_id):
                            current = self.store._get(db, turn.workspace_id, turn.id)
                            current.status = "stopping"
                            self.store._save(db, current)
                    if client is not None:
                        await client.close()
        except asyncio.CancelledError:
            status = "stopped"
            notice = "Stopped. Saved task changes remain; workers continue independently."
        except ApplicationError as error:
            notice = str(error)
            if error.code == "agent_resume_failed":
                with workspace.connection(write=True) as db:
                    if self.store.owns(db, turn.workspace_id, turn.id, generation_id):
                        current = self.store._get(db, turn.workspace_id, turn.id)
                        current.session = "unavailable"
                        self.store._save(db, current)
        except Exception:
            notice = (
                "The coordinator disconnected or could not complete this turn. "
                "Check the host harness and try again."
            )
        finally:
            self.finishing.add(turn.id)
            # Startup can fail before entering the scoped endpoint. Fence Stop here too,
            # so a late request cannot cancel the recorder while it commits final output.
            with workspace.connection(write=True) as db:
                if self.store.owns(db, turn.workspace_id, turn.id, generation_id):
                    current = self.store._get(db, turn.workspace_id, turn.id)
                    current.status = "stopping"
                    self.store._save(db, current)
            if temporary is not None:
                temporary.cleanup()
            if startup_pending:
                recorder.emit(
                    ActivityUpdate(
                        key="session",
                        kind="status",
                        text="Fresh agent session startup stopped."
                        if status == "stopped"
                        else "Fresh agent session did not receive this message.",
                    )
                )
            Usage(workspace).end(
                turn.workspace_id,
                "coordinator_turn",
                turn.id,
                confirmed=client is None or client.cleanup_confirmed,
            )
            await recorder.close()
            with workspace.connection(write=True) as db:
                if self.store.owns(db, turn.workspace_id, turn.id, generation_id):
                    current = self.store._get(db, turn.workspace_id, turn.id)
                    self.store.sessions.finish(
                        db,
                        turn.workspace_id,
                        turn.id,
                        cleanup_confirmed=client is None or client.cleanup_confirmed,
                        generation_id=generation_id,
                    )
                    if client is not None and not client.cleanup_confirmed:
                        current.status = "uncertain"
                        current.notice = (
                            "Native cleanup could not be confirmed. Stop any remaining "
                            "coordinator process on the host, then confirm it stopped."
                        )
                    else:
                        current.status = status
                        current.notice = notice
                    self.store._save(db, current)

    async def stop(self, project: str, identity: str) -> CoordinatorTurn:
        with self.supervisor.workspace.connection(write=True) as db:
            current = self.store._get(db, project, identity)
            job = self.jobs.get(identity)
            if job and current.status in ("starting", "running"):
                current.status = "stopping"
                self.store._save(db, current)
                # Let a just-created task enter its finally block before cancellation.
                awaitable = job
            else:
                return current
        await asyncio.sleep(0)
        if not awaitable.done() and identity not in self.finishing:
            awaitable.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(awaitable)
        return self.store.get(project, identity)

    async def close(self) -> None:
        self.closing = True
        for _, _, job in self.command_jobs.values():
            if not job.done():
                job.cancel()
        await asyncio.gather(
            *(job for _, _, job in self.command_jobs.values()), return_exceptions=True
        )
        self.command_jobs.clear()
        for identity in list(self.jobs):
            with self.supervisor.workspace.connection() as db:
                row = db.execute(
                    "SELECT workspace_id FROM coordinator_turns WHERE id=?", (identity,)
                ).fetchone()
            if row:
                await self.stop(row[0], identity)
        if self.jobs:
            await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
