"""Bounded public attempt activity. Never used for ownership, context or approval."""

import asyncio
import re
import sqlite3
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field

from flowfield.activity_text import preview, retain
from flowfield.errors import ApplicationError
from flowfield.execution_models import Usage

if TYPE_CHECKING:
    from flowfield.application import Workspace

Kind = Literal["agent", "tool", "command", "output", "status"]
MAX_ENTRIES = 100
MAX_TEXT = 6000
MAX_TOTAL = 60000
SCHEMA = """
CREATE TABLE run_activity (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), revision INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


class ContextUsage(BaseModel):
    used: int = Field(ge=0)
    size: int = Field(gt=0)


class ActivityUpdate(BaseModel):
    key: str = Field(max_length=100)
    kind: Kind
    text: str
    append: bool = False
    omitted: bool = False
    context: ContextUsage | None = None


class RunActivityEntry(BaseModel):
    key: str
    kind: Kind
    text: str
    omitted: bool = False
    preview: str = ""
    abridged: bool = False


class RunActivityPage(BaseModel):
    revision: int = 0
    supported: bool = False
    active: bool = False
    changed: bool = True
    omitted: bool = False
    items: list[RunActivityEntry] = []
    usage: Usage = Field(default_factory=Usage)
    context: ContextUsage | None = None


def clean(text: str) -> str:
    # Terminal control sequences must never become browser commands or presentation.
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)


def update_activity(page: RunActivityPage, updates: list[ActivityUpdate]) -> None:
    entries = {entry.key: entry for entry in page.items}
    for update in updates:
        if update.context is not None:
            page.context = update.context
            continue
        old = entries.get(update.key)
        text = (old.text if old and update.append else "") + clean(update.text)
        omitted = update.omitted or len(text) > MAX_TEXT or bool(old and old.omitted)
        entries[update.key] = RunActivityEntry(
            key=update.key, kind=update.kind, text=retain(text, MAX_TEXT), omitted=omitted
        )
    page.items = list(entries.values())
    while len(page.items) > MAX_ENTRIES or sum(len(e.text) for e in page.items) > MAX_TOTAL:
        page.items.pop(0)
        page.omitted = True
    page.revision += 1
    page.supported = True


class ActivityStore(Protocol):
    def write(self, project: str, run: str, updates: list[ActivityUpdate]) -> None: ...


class RunActivity:
    def __init__(self, workspace: "Workspace"):
        self.workspace = workspace

    def write(self, project: str, run: str, updates: list[ActivityUpdate]) -> None:
        # Output does not invalidate the entire board. Mounted panes read their own revision.
        with self.workspace.connection(write=True, notify=False) as db:
            owner = db.execute(
                "SELECT status FROM runs WHERE project_id=? AND id=?", (project, run)
            ).fetchone()
            if not owner:
                raise ApplicationError("not_found", "Attempt not found.", 404)
            if owner[0] not in ("preparing", "running", "stopping"):
                return  # Late output cannot mutate a closed/uncertain attempt.
            saved = db.execute("SELECT data FROM run_activity WHERE run_id=?", (run,)).fetchone()
            page = RunActivityPage.model_validate_json(saved[0]) if saved else RunActivityPage()
            update_activity(page, updates)
            db.execute(
                "INSERT INTO run_activity VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "revision=excluded.revision,data=excluded.data",
                (run, page.revision, page.model_dump_json()),
            )

        if self.workspace.on_activity:
            self.workspace.on_activity(project, run)

    def read(self, project: str, run: str, after: int = -1) -> RunActivityPage:
        with self.workspace.connection() as db:
            owner = db.execute(
                "SELECT status,json_extract(data,'$.usage') FROM runs WHERE project_id=? AND id=?",
                (project, run),
            ).fetchone()
            if not owner:
                raise ApplicationError("not_found", "Attempt not found.", 404)
            saved = db.execute("SELECT data FROM run_activity WHERE run_id=?", (run,)).fetchone()
            page = RunActivityPage.model_validate_json(saved[0]) if saved else RunActivityPage()
            page.active = owner[0] in ("preparing", "running", "stopping")
            page.usage = Usage.model_validate_json(owner[1]) if owner[1] else Usage()
            page.changed = after != page.revision
            if not page.changed:
                page.items = []
            for entry in page.items:
                entry.preview = preview(entry.text, entry.kind)
                entry.abridged = entry.preview != entry.text
            return page


class ActivityRecorder:
    """Coalesce deltas before SQLite; bound queued output even when storage is slow."""

    def __init__(
        self,
        workspace: "Workspace",
        project: str,
        run: str,
        *,
        store: ActivityStore | None = None,
        preserve_prose: bool = False,
    ):
        self.store, self.project, self.run = store or RunActivity(workspace), project, run
        self.preserve_prose = preserve_prose
        self.pending: list[ActivityUpdate] = []
        self.lost = False
        self.lost_prose = False
        self.closed = False
        self.flush_lock = asyncio.Lock()
        self.job = asyncio.create_task(self._pump())

    def emit(self, update: ActivityUpdate) -> None:
        if self.closed:
            return
        # Native peers may send hundreds of tiny deltas without yielding. Bound
        # complete entries rather than dropping the beginning of a streamed reply.
        previous = next(
            (
                item
                for item in self.pending
                if (item.context is not None and update.context is not None)
                or (item.context is None and update.context is None and item.key == update.key)
            ),
            None,
        )
        text = (previous.text if previous and update.append else "") + update.text
        preserve = self.preserve_prose and update.kind == "agent"
        combined = update.model_copy(
            update={
                "text": text if preserve else retain(text, MAX_TEXT),
                "append": previous.append if previous and update.append else update.append,
                "omitted": update.omitted
                or (not preserve and len(text) > MAX_TEXT)
                or bool(previous and previous.omitted),
            }
        )
        if previous is not None:
            self.pending[self.pending.index(previous)] = combined
        else:
            self.pending.append(combined)
        # Coordinator prose goes to durable storage before activity is abridged.
        # Native transports bound total turn output; presentation limits apply only
        # to discardable activity here, never to the conversation itself.
        discardable = [
            item for item in self.pending if not (self.preserve_prose and item.kind == "agent")
        ]
        while (
            len(discardable) > MAX_ENTRIES
            or sum(len(item.text) for item in discardable) > MAX_TOTAL
        ):
            self.pending.remove(discardable.pop(0))
            self.lost = True

    async def flush(self) -> None:
        # A manual/final flush must not race the pump and reorder append writes.
        async with self.flush_lock:
            await self._flush()

    async def _flush(self) -> None:
        updates, self.pending = self.pending, []
        if self.lost:
            updates.insert(
                0,
                ActivityUpdate(
                    key="stream-gap"
                    if not self.preserve_prose or self.lost_prose
                    else "activity-gap",
                    kind="status",
                    text="Some activity was omitted during interrupted or rapid output delivery.",
                    omitted=True,
                ),
            )
            self.lost = False
            self.lost_prose = False
        if updates:
            try:
                await asyncio.to_thread(self.store.write, self.project, self.run, updates)
            except sqlite3.OperationalError:
                # Activity must not stop execution or grow an unbounded retry queue.
                # A later successful flush records the gap explicitly.
                self.lost = True
                self.lost_prose |= any(
                    item.kind == "agent" or item.key == "stream-gap" for item in updates
                )

    async def _pump(self) -> None:
        while not self.closed:
            await asyncio.sleep(0.1)
            await self.flush()

    async def close(self) -> None:
        self.closed = True
        await self.job
        await self.flush()
