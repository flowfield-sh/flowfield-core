"""Coalesced live invalidations; SQLite remains the durable source of truth."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class Pending:
    workspaces: set[str] | None = field(default_factory=set)
    activity: set[tuple[str, str]] = field(default_factory=set)
    coordinator_activity: set[tuple[str, str]] = field(default_factory=set)


class Changes:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.subscribers: set[asyncio.Queue[Pending]] = set()

    def publish(self, workspace_id: str | None = None) -> None:
        # Application writes also run in worker threads. Only touch queues on the loop.
        self.loop.call_soon_threadsafe(self._notify, workspace_id)

    def publish_activity(self, workspace_id: str, attempt_id: str) -> None:
        self.loop.call_soon_threadsafe(self._notify, workspace_id, attempt_id)

    def publish_coordinator_activity(self, workspace_id: str, turn_id: str) -> None:
        self.loop.call_soon_threadsafe(self._notify, workspace_id, turn_id, True)

    def _notify(
        self, workspace_id: str | None, attempt_id: str | None = None, coordinator: bool = False
    ) -> None:
        for queue in self.subscribers:
            pending = Pending() if queue.empty() else queue.get_nowait()
            if attempt_id is not None and workspace_id is not None:
                target = pending.coordinator_activity if coordinator else pending.activity
                target.add((workspace_id, attempt_id))
                if len(pending.activity) + len(pending.coordinator_activity) > 100:
                    pending.workspaces = None
                    pending.activity.clear()
                    pending.coordinator_activity.clear()
            elif workspace_id is None:
                pending.workspaces = None
            elif pending.workspaces is not None:
                pending.workspaces.add(workspace_id)
            queue.put_nowait(pending)

    async def events(self) -> AsyncIterator[dict[str, str | int]]:
        queue: asyncio.Queue[Pending] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(
            Pending(workspaces=None)
        )  # Every connection resyncs, including after a restart.
        try:
            while True:
                pending = await queue.get()
                if pending.workspaces is None or pending.workspaces:
                    yield {
                        "event": "change",
                        "data": json.dumps(
                            {
                                "workspaces": sorted(pending.workspaces)
                                if pending.workspaces is not None
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
