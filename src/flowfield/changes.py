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
    usage: set[str] | None = field(default_factory=set)


class Changes:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.subscribers: set[asyncio.Queue[Pending]] = set()
        self._usage_pending: set[str] | None = set()
        self._usage_timer: asyncio.TimerHandle | None = None

    def publish_usage(self, workspace_id: str | None = None) -> None:
        self.loop.call_soon_threadsafe(self._queue_usage, workspace_id)

    def _queue_usage(self, workspace_id: str | None) -> None:
        if workspace_id is None:
            self._usage_pending = None
        elif self._usage_pending is not None:
            self._usage_pending.add(workspace_id)
            if len(self._usage_pending) > 100:
                self._usage_pending = None
        if self._usage_timer is None:
            self._usage_timer = self.loop.call_later(0.25, self._flush_usage)

    def _flush_usage(self) -> None:
        for queue in self.subscribers:
            pending = Pending() if queue.empty() else queue.get_nowait()
            if self._usage_pending is None:
                pending.usage = None
            elif pending.usage is not None:
                pending.usage.update(self._usage_pending)
                if len(pending.usage) > 100:
                    pending.usage = None
            queue.put_nowait(pending)
        self._usage_pending = set()
        self._usage_timer = None

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

    async def events(self, *, presence_interval: float = 2) -> AsyncIterator[dict[str, str | int]]:
        queue: asyncio.Queue[Pending] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(
            Pending(workspaces=None)
        )  # Every connection resyncs, including after a restart.
        try:
            next_presence = self.loop.time() + presence_interval
            while True:
                try:
                    pending = await asyncio.wait_for(
                        queue.get(), timeout=max(0, next_presence - self.loop.time())
                    )
                except TimeoutError:
                    pending = Pending()
                if self.loop.time() >= next_presence:
                    # A shared wake-up for short presence reads; no data invalidation.
                    next_presence = self.loop.time() + presence_interval
                    yield {"event": "notification", "data": "{}"}
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
                if pending.usage is None or pending.usage:
                    yield {
                        "event": "usage",
                        "data": json.dumps(
                            {
                                "workspaces": sorted(pending.usage)
                                if pending.usage is not None
                                else None
                            }
                        ),
                    }
        finally:
            self.subscribers.remove(queue)
