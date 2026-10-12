"""Contextual delivery receipts, never an inbox or authority over work."""

import json
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from flowfield.attention import actionable_attention
from flowfield.browser_links import project_href

if TYPE_CHECKING:
    from flowfield.application import Workspace

PRESENCE_TTL = 10
FRESHNESS = 60
SETTLE_SECONDS = 2
RETAINED_EVENTS = 1000
MAX_CLIENTS = 32


class AnnouncementSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_enabled: bool = False
    work_enabled: bool = True
    updates_enabled: bool = True


class Presence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    active: bool
    system: Literal["browser", "native"] = "browser"
    permitted: bool = False
    visible_sources: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=100
    )
    manual_update: bool = False


class Announcement(BaseModel):
    id: int
    source_key: str
    title: str
    message: str
    href: str
    project_id: str | None = None
    kind: Literal["work", "update"]


class Delivery(BaseModel):
    channel: Literal["toast", "system"] | None = None
    system_allowed: bool = False
    items: list[Announcement] = Field(default_factory=list)


def publish(
    db: sqlite3.Connection,
    key: str,
    project: str | None,
    kind: str,
    title: str,
    message: str,
    href: str,
    timestamp: float,
    exclude_clients: list[str] | None = None,
) -> None:
    db.execute(
        "INSERT INTO announcement_events(source_key,project_id,kind,data,created_at) VALUES "
        "(?,?,?,?,?)",
        (
            key,
            project,
            kind,
            json.dumps(
                {
                    "title": title,
                    "message": message,
                    "href": href,
                    "exclude_clients": exclude_clients or [],
                }
            ),
            timestamp,
        ),
    )

    db.execute(
        "DELETE FROM announcement_events WHERE id NOT IN (SELECT id FROM "
        "announcement_events ORDER BY id DESC LIMIT ?)",
        (RETAINED_EVENTS,),
    )


def announce_update(
    db: sqlite3.Connection, version: str, timestamp: float, exclude_clients: list[str] | None = None
) -> None:
    db.execute(
        "UPDATE announcement_events SET state='resolved' WHERE kind='update' AND state='pending'"
    )
    publish(
        db,
        f"update:{version}",
        None,
        "update",
        f"Flowfield {version} is available",
        "View release notes and update options.",
        "/settings/updates",
        timestamp,
        exclude_clients,
    )


def reconcile_attention(
    db: sqlite3.Connection, project_id: str | None = None, timestamp: float | None = None
) -> None:
    """Observe transitions in the domain transaction, including rapid resolve/reopen cycles."""
    timestamp = time.time() if timestamp is None else timestamp
    projects = db.execute(
        "SELECT id,name FROM projects" + (" WHERE id=?" if project_id else ""),
        (project_id,) if project_id else (),
    ).fetchall()
    for project in projects:
        current: set[str] = set()
        previous = dict(
            db.execute(
                "SELECT key,signature FROM attention_sources WHERE project_id=?", (project["id"],)
            )
        )
        # Iterate the complete projection in one transaction; no presentation-page truncation.
        for row in actionable_attention(db, project["id"]):
            kind, identity = row["kind"], row["id"]
            key = f"{project['id']}:{kind}:{identity}"
            current.add(key)
            signature = row["status"]
            if previous.get(key) == signature:
                continue
            db.execute(
                "UPDATE announcement_events SET state='resolved' WHERE source_key=? AND "
                "state='pending'",
                (key,),
            )
            base = project_href(db, project["id"])
            task = f"{base}/tasks/{quote(row['task_key'], safe='')}" if row["task_key"] else base
            label = "Needs your attention"
            if kind == "question":
                revision = db.execute(
                    "SELECT json_extract(data,'$.revision') FROM questions WHERE "
                    "project_id=? AND id=?",
                    (project["id"], identity),
                ).fetchone()[0]
                href = (
                    f"{task}/conversation/question:{identity}:{revision}"
                    if row["task_key"]
                    else f"{base}/inbox/{quote(identity, safe='')}"
                )
                label = "Needs your answer"
            elif kind == "permission":
                href = f"{base}/inbox?permission={quote(identity, safe='')}"
                label = "Review tool permission"
            elif kind == "result":
                href = f"{task}/conversation/result:{identity}"
                label = "Review current result"
            elif kind == "integration":
                href = f"{task}/history/{row['run_id']}/integrations/{identity}"
            else:
                href = f"{task}/conversation/attempt:{row['run_id']}"
            publish(
                db,
                key,
                project["id"],
                "work",
                f"{project['name']} · {row['title']}",
                label,
                href,
                timestamp,
            )
            db.execute(
                "INSERT INTO attention_sources VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                "SET signature=excluded.signature",
                (key, project["id"], signature),
            )
        for key in previous.keys() - current:
            db.execute("DELETE FROM attention_sources WHERE key=?", (key,))
            db.execute(
                "UPDATE announcement_events SET state='resolved' WHERE source_key=? AND "
                "state='pending'",
                (key,),
            )


def preferences(db: sqlite3.Connection) -> AnnouncementSettings:
    row = db.execute("SELECT data FROM announcement_preferences WHERE id=1").fetchone()
    return AnnouncementSettings.model_validate_json(row[0]) if row else AnnouncementSettings()


class Announcements:
    def __init__(self, workspace: "Workspace", clock: Callable[[], float] = time.time):
        self.workspace, self.clock = workspace, clock

    def settings(self) -> AnnouncementSettings:
        with self.workspace.connection() as db:
            return preferences(db)

    def configure(
        self, value: AnnouncementSettings, *, partial: bool = False
    ) -> AnnouncementSettings:
        with self.workspace.connection(write=True, notify=False) as db:
            previous = preferences(db)
            if partial:
                value = previous.model_copy(update=value.model_dump(exclude_unset=True))
            # Changed preferences apply to future events, not a queue of earlier popups.
            for kind, changed in (
                ("work", previous.work_enabled != value.work_enabled),
                ("update", previous.updates_enabled != value.updates_enabled),
            ):
                if changed:
                    db.execute(
                        "UPDATE announcement_events SET state='suppressed' "
                        "WHERE state='pending' AND kind=?",
                        (kind,),
                    )
            active = any(
                Presence.model_validate_json(row[0]).active
                for row in db.execute(
                    "SELECT data FROM announcement_clients WHERE seen_at>=?",
                    (self.clock() - PRESENCE_TTL,),
                )
            )
            if previous.system_enabled != value.system_enabled and not active:
                db.execute(
                    "UPDATE announcement_events SET state='suppressed' WHERE state='pending'"
                )
            db.execute(
                "INSERT INTO announcement_preferences VALUES (1,?) ON CONFLICT(id) DO "
                "UPDATE SET data=excluded.data",
                (value.model_dump_json(),),
            )
        return value

    def start(self) -> None:
        with self.workspace.connection(write=True, notify=False) as db:
            reconcile_attention(db, timestamp=self.clock())
            db.execute("DELETE FROM announcement_clients")
            db.execute("UPDATE announcement_events SET state='expired' WHERE state='pending'")
            self._prune(db, self.clock())

    def _prune(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM announcement_clients WHERE seen_at<?", (now - PRESENCE_TTL,))
        db.execute(
            "UPDATE announcement_events SET state='expired' WHERE state='pending' AND created_at<?",
            (now - FRESHNESS,),
        )
        db.execute(
            "DELETE FROM announcement_events WHERE state!='pending' AND id NOT IN (SELECT "
            "id FROM announcement_events ORDER BY id DESC LIMIT ?)",
            (RETAINED_EVENTS,),
        )

    def poll(self, presence: Presence) -> Delivery:
        now = self.clock()
        with self.workspace.connection(write=True, notify=False) as db:
            self._prune(db, now)
            previous = db.execute(
                "SELECT * FROM announcement_clients WHERE id=?", (presence.client_id,)
            ).fetchone()
            if not previous:
                count = db.execute("SELECT count(*) FROM announcement_clients").fetchone()[0]
                if count >= MAX_CLIENTS:
                    return Delivery()
                if count == 0:
                    db.execute(
                        "UPDATE announcement_events SET state='expired' WHERE state='pending'"
                    )
            joined = previous["joined_at"] if previous else now
            prior = Presence.model_validate_json(previous["data"]) if previous else None
            if (
                prior
                and not prior.active
                and (presence.active or (not prior.permitted and presence.permitted))
            ):
                # Returning to the app or granting permission does not replay missed work.
                joined = now
            active_since = (
                previous["active_since"] if prior and prior.active == presence.active else now
            )
            db.execute(
                "INSERT INTO announcement_clients VALUES (?,?,?,?,?) ON CONFLICT(id) DO "
                "UPDATE SET seen_at=excluded.seen_at, joined_at=excluded.joined_at, "
                "active_since=excluded.active_since, "
                "data=excluded.data",
                (presence.client_id, now, joined, active_since, presence.model_dump_json()),
            )
            settings = preferences(db)
            clients = [
                (row, Presence.model_validate_json(row["data"]))
                for row in db.execute("SELECT * FROM announcement_clients")
            ]
            eligible = [
                (row, client)
                for row, client in clients
                if client.active or (settings.system_enabled and client.permitted)
            ]
            if not eligible:
                return Delivery()
            winner, client = max(
                eligible,
                key=lambda entry: (
                    entry[1].active,
                    entry[1].system == "native" if not entry[1].active else False,
                    entry[0]["active_since"],
                    entry[0]["id"],
                ),
            )
            if client.client_id != presence.client_id:
                return Delivery()
            channel: Literal["toast", "system"] = "toast" if client.active else "system"
            oldest = db.execute(
                "SELECT min(created_at) FROM announcement_events WHERE state='pending'"
            ).fetchone()[0]
            if oldest is None or oldest > now - SETTLE_SECONDS:
                return Delivery(channel=channel)
            # A claim is a single presentation attempt, not evidence that a human saw it.
            # Settle the batch from its first event; include its newer neighbors so a
            # poll boundary cannot split a simultaneous burst into individual alerts.
            items = []
            for row in db.execute(
                "SELECT * FROM announcement_events WHERE state='pending' AND created_at<=? "
                "ORDER BY id LIMIT 200",
                (now,),
            ).fetchall():
                suppressed = (
                    client.client_id in json.loads(row["data"]).get("exclude_clients", [])
                    or row["created_at"] < winner["joined_at"]
                    or (row["kind"] == "work" and not settings.work_enabled)
                    or (
                        row["kind"] == "update"
                        and (not settings.updates_enabled or client.manual_update)
                    )
                    or (client.active and row["source_key"] in client.visible_sources)
                )
                db.execute(
                    "UPDATE announcement_events SET state=?,client_id=?,channel=? WHERE id=?",
                    (
                        "suppressed" if suppressed else "attempted",
                        client.client_id,
                        channel,
                        row["id"],
                    ),
                )
                if not suppressed:
                    items.append(
                        Announcement(
                            id=row["id"],
                            source_key=row["source_key"],
                            project_id=row["project_id"],
                            kind=row["kind"],
                            **json.loads(row["data"]),
                        )
                    )
            return Delivery(
                channel=channel,
                system_allowed=settings.system_enabled and client.permitted,
                items=items,
            )
