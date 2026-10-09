"""Explicit coordinator turns; independent of worker scheduling and capacity."""

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from flowfield.activity_text import retain
from flowfield.adapters.acp_permissions import permission_handler
from flowfield.adapters.agent_mcp import serve_scope
from flowfield.adapters.codex_agent import CodexAgent, command_options
from flowfield.adapters.harness_host import resolve
from flowfield.adapters.local_execution import LocalHost
from flowfield.agent_models import AgentCommand
from flowfield.agent_settings import AgentSettings
from flowfield.agent_tools import coordinator_scope
from flowfield.attachments import Attachments
from flowfield.coordinator_models import CoordinatorSend, CoordinatorTurn
from flowfield.coordinator_store import CoordinatorStore
from flowfield.errors import ApplicationError
from flowfield.harness_settings import HarnessSettings
from flowfield.reads import size
from flowfield.run_activity import ActivityRecorder, ActivityUpdate

if TYPE_CHECKING:
    from flowfield.supervisor import Supervisor

GUIDANCE = """You are this project's Flowfield coordinator. Help the human shape intent,
prepare tasks and resolve saved input. Native session history carries this conversation;
any recent_conversation field is a one-time handoff from earlier Flowfield chat.
Read get_project and get_board first; canonical Flowfield state takes precedence over old
messages. Follow full-text and pagination links before editing, and read current revisions
before every consequential write.
A selected_task is the human's explicit focus for this message, captured at send time.
Read that task through MCP before acting; it is context, not a replacement assignment or
permission to change active work. If a result_id is present, discuss that exact result.
Without selected_task, do not assume an earlier selection is still the human's focus.
Use only the named scoped MCP connection for Flowfield operations. Do not use ambient
Flowfield connections, CLI or database files. Project identity is already bound to these tools.
Use the host's tools under the selected native access mode. You may perform explicitly
requested initial project or environment setup directly, including configuration and dependencies.
When asked to set up this project for workers, read integration and inspection settings,
save the agreed destination and trusted setup/check/run commands, then use validate_project_setup.
This checks a separate checkout without a model call or enabling the queue. Report actual
validation evidence and remaining blockers. Repository guidance installation is optional for
this built-in workflow; never use an ambient connection to work around missing scoped tools.
Preserve existing files and dirty work; check active workers before changing shared resources.
Use managed tasks for agreed implementation work rather than editing their code in the project
checkout. Never modify worker worktrees or launch workers outside Flowfield's scheduler.
Capture agreed work in existing tasks when possible. Brainstorming is not authorization.
Task descriptions explain the desired outcome and completion conditions. Milestones group
tasks; only tasks have dependencies. Respect repository-owned instructions and decisions;
Flowfield does not replace the project’s architecture/design/convention records.
Prepare assignments with create_task/edit_task when intent is clear. Prepared is not started.
Prioritizing into Up next
can make work eligible for an enabled queue: do this only when the human authorized scheduling.
Use apply_answer to reconcile already saved coordinator-owned answers; managed worker answers
are delivered by the service. You have the complete project toolset: answer questions,
configure workers, control the queue, inspect results, request changes and recover work
when authorized. Use get_task_input and reply_to_task to send the human's answers or feedback.
Never invent an answer or infer code approval. When the human explicitly approves a specific
result, read that exact current result and use review_result with its revision and candidate.
No second browser confirmation is needed. Changed candidates require fresh approval.
The service delivers approved results; verify delivery before claiming Done.
Stop ends this coordinator turn, not workers; use stop_run for an authorized worker stop.
After an interrupted turn inspect canonical state before repeating any operation: completed
writes remain committed. Explain useful results and concrete next actions concisely. Link tasks
with Markdown using /projects/{project_id}/tasks/{task_key}. Never claim unsupported actions.
"""


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
        cwd = workspace.project(project).path
        settings = AgentSettings(workspace).get(project, "coordinator")
        if not settings.effective:
            raise ApplicationError(
                "coordinator_settings", "Choose coordinator settings first.", 409
            )
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
        job = asyncio.create_task(
            command_options(workspace.directory, Path(cwd), choice, registration=registration)
        )
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

    def _prompt(self, turn: CoordinatorTurn, server: str, *, resumed: bool = False) -> str:
        instructions = {
            "instructions": f"Use the {server} MCP connection.\n" + GUIDANCE,
            "project_id": turn.project_id,
            "flowfield_connection": server,
            "human_message": turn.text,
            "selected_task": turn.task_context.model_dump() if turn.task_context else None,
        }
        if resumed:
            return json.dumps(instructions, ensure_ascii=False)
        page = self.store.page(turn.project_id, turn.conversation_id, before=turn.number)
        history: list[dict[str, str]] = []
        remaining = 48000 - 2  # JSON array delimiters; budget encoded UTF-8, not characters.
        abridged = False
        for previous in reversed(page.items):
            entry = {
                "human": previous.text,
                "selected_task": previous.task_context.model_dump_json()
                if previous.task_context
                else "",
                "status": previous.status,
                "coordinator": "\n\n".join(
                    e.text for e in previous.activity.items if e.kind == "agent"
                ),
            }
            if len(history) >= 8:
                break
            if size(entry) + 2 > remaining:
                if history:
                    break
                # Always carry the latest exchange, even when its output exceeds the
                # entire handoff budget. Retain both the opening and conclusion.
                original = dict(entry)
                limit = max(len(entry["human"]), len(entry["coordinator"]))
                while size(entry) + 2 > remaining:
                    limit //= 2
                    for field in ("human", "coordinator"):
                        entry[field] = retain(original[field], limit)
                abridged = True
            abridged |= previous.activity.omitted or any(
                item.omitted for item in previous.activity.items if item.kind == "agent"
            )
            history.insert(0, entry)
            remaining -= size(entry) + 2
        return json.dumps(
            {
                **instructions,
                "history_is_partial": bool(
                    abridged or page.next_before or len(history) < len(page.items)
                ),
                "recent_conversation": history,
                "human_message": turn.text,
            },
            ensure_ascii=False,
        )

    async def _run(self, turn: CoordinatorTurn) -> None:
        workspace = self.supervisor.workspace
        client: CodexAgent | None = None
        temporary: tempfile.TemporaryDirectory[str] | None = None
        recorder = ActivityRecorder(workspace, turn.project_id, turn.id, store=self.store)
        status: Literal["completed", "failed", "stopped"] = "failed"
        notice = ""
        try:
            project = workspace.project(turn.project_id)
            grant = await coordinator_scope(
                self.supervisor, turn.project_id, author=f"coordinator:{turn.id}"
            )
            # The same name replaces the previous endpoint on resume; credentials
            # and grants remain fresh and are revoked at the end of every turn.
            async with serve_scope(grant, name="flowfield_" + turn.conversation_id) as server:
                try:
                    project = workspace.project(turn.project_id)
                    temporary = tempfile.TemporaryDirectory(prefix="flowfield-coordinator-")
                    environment = LocalHost(os.environ).launch_environment(
                        Path(project.path), Path(temporary.name)
                    )
                    client = CodexAgent(
                        workspace.directory,
                        Path(project.path),
                        environment,
                        registration=turn.settings.registration,
                    )
                    session_id = self.store.session(
                        turn.project_id,
                        turn.settings.choice.harness,
                        project.path,
                        launch=client.launch,
                    )
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        current.native_started = True
                        current.launch = client.launch
                        self.store._save(db, current)
                    await client.start([server], resume=session_id, persistent=True)
                    applied = await client.configure(turn.settings.choice)
                    attachments = Attachments(workspace).inputs(turn.project_id, None, turn.text)
                    # Reject unsupported input before saving a new session identity:
                    # native history is only materialized by its first prompt.
                    client.validate_attachments(attachments)
                    native_command = await client.command_prompt(
                        turn.text, has_history=session_id is not None, attachments=attachments
                    )
                    assert client.session.session_id
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
                        if current.status != "starting":
                            status = "stopped"
                            return
                        current.status = "running"
                        current.session = "resumed" if session_id else "new"
                        current.applied = turn.settings.model_copy(update={"choice": applied})
                        if not native_command:
                            db.execute(
                                "INSERT INTO coordinator_sessions VALUES (?,?,?,?,?) "
                                "ON CONFLICT(project_id) DO NOTHING",
                                (
                                    turn.project_id,
                                    turn.settings.choice.harness,
                                    client.session.session_id,
                                    project.path,
                                    client.launch.model_dump_json() if client.launch else None,
                                ),
                            )
                            if client.launch:
                                db.execute(
                                    "UPDATE coordinator_sessions SET launch=? WHERE project_id=? "
                                    "AND launch IS NULL",
                                    (client.launch.model_dump_json(), turn.project_id),
                                )
                        self.store._save(db, current)
                    prompt = native_command or self._prompt(
                        turn, server.name, resumed=session_id is not None
                    )
                    handoff = (
                        [] if native_command else json.loads(prompt).get("recent_conversation", [])
                    )
                    session_note = (
                        "Agent session resumed with native conversation history."
                        if session_id
                        else "Checking Codex without starting a conversation."
                        if native_command
                        else "New agent session started."
                    )
                    if handoff:
                        session_note += (
                            f" Included {len(handoff)} recent saved exchanges; "
                            "earlier native tool history is not available in this session."
                        )
                    if handoff or native_command:
                        recorder.emit(
                            ActivityUpdate(key="session", kind="status", text=session_note)
                        )
                    client.on_activity = recorder.emit
                    assert client.session.session_id
                    async with self.supervisor.permissions.turn(
                        turn.project_id,
                        "coordinator",
                        session_id=client.session.session_id,
                        turn_id=turn.id,
                        conversation_id=turn.conversation_id,
                    ) as permission_turn:
                        outcome = await client.prompt(
                            prompt,
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
                    with workspace.connection(write=True, project_id=turn.project_id) as db:
                        current = self.store._get(db, turn.project_id, turn.id)
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
                with workspace.connection(write=True, project_id=turn.project_id) as db:
                    current = self.store._get(db, turn.project_id, turn.id)
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
            with workspace.connection(write=True, project_id=turn.project_id) as db:
                current = self.store._get(db, turn.project_id, turn.id)
                current.status = "stopping"
                self.store._save(db, current)
            if temporary is not None:
                temporary.cleanup()
            await recorder.close()
            with workspace.connection(write=True, project_id=turn.project_id) as db:
                current = self.store._get(db, turn.project_id, turn.id)
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
        with self.supervisor.workspace.connection(write=True, project_id=project) as db:
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
                    "SELECT project_id FROM coordinator_turns WHERE id=?", (identity,)
                ).fetchone()
            if row:
                await self.stop(row[0], identity)
        if self.jobs:
            await asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
