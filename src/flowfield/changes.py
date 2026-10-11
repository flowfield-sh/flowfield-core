"""Coalesced live invalidations; SQLite remains the durable source of truth."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class Pending:
    projects: set[str] | None = field(default_factory=set)
    activity: set[tuple[str, str]] = field(default_factory=set)
    coordinator_activity: set[tuple[str, str]] = field(default_factory=set)


class Changes:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.subscribers: set[asyncio.Queue[Pending]] = set()

    def publish(self, project_id: str | None = None) -> None:
        # Application writes also run in worker threads. Only touch queues on the loop.
        self.loop.call_soon_threadsafe(self._notify, project_id)

    def publish_activity(self, project_id: str, attempt_id: str) -> None:
        self.loop.call_soon_threadsafe(self._notify, project_id, attempt_id)

    def publish_coordinator_activity(self, workspace_id: str, turn_id: str) -> None:
        self.loop.call_soon_threadsafe(self._notify, workspace_id, turn_id, True)

    def _notify(
        self, project_id: str | None, attempt_id: str | None = None, coordinator: bool = False
    ) -> None:
        for queue in self.subscribers:
            pending = Pending() if queue.empty() else queue.get_nowait()
            if attempt_id is not None and project_id is not None:
                target = pending.coordinator_activity if coordinator else pending.activity
                target.add((project_id, attempt_id))
                if len(pending.activity) + len(pending.coordinator_activity) > 100:
                    pending.projects = None
                    pending.activity.clear()
                    pending.coordinator_activity.clear()
            elif project_id is None:
                pending.projects = None
            elif pending.projects is not None:
                pending.projects.add(project_id)
            queue.put_nowait(pending)

    async def events(self) -> AsyncIterator[dict[str, str | int]]:
        queue: asyncio.Queue[Pending] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(
            Pending(projects=None)
        )  # Every connection resyncs, including after a restart.
        try:
            while True:
                pending = await queue.get()
                if pending.projects is None or pending.projects:
                    yield {
                        "event": "change",
                        "data": json.dumps(
                            {
                                "projects": sorted(pending.projects)
                                if pending.projects is not None
                                else None
                            }
                        ),
                        "retry": 1000,
                    }
                if pending.activity or pending.coordinator_activity:
                    yield {
                        "event": "activity",
                        "data": json.dumps(
                            {
                                "activity": sorted(pending.activity),
                                **(
                                    {"coordinator_activity": sorted(pending.coordinator_activity)}
                                    if pending.coordinator_activity
                                    else {}
                                ),
                            }
                        ),
                    }
        finally:
            self.subscribers.remove(queue)
