"""Service lifetime, contextual delivery and independent update controls."""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from flowfield.announcements import Announcements, AnnouncementSettings, Delivery, Presence
from flowfield.application import Workspace
from flowfield.updates import Updates, UpdateStatus


class UpdateCheck(BaseModel):
    client_id: str | None = Field(default=None, max_length=64)
    reason: Literal["startup", "manual"] = "manual"


class UpdateSettings(BaseModel):
    automatic: bool


class NotificationService:
    def __init__(self, workspace: Workspace, updates: Updates):
        self.announcements = Announcements(workspace)
        self.updates = updates
        self.stopped = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.announcements.start()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.updates.trigger("hourly")
            except Exception:
                logging.getLogger(__name__).exception("Update scheduling failed; retrying")
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=3)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self.stopped.set()
        if self.task:
            await self.task


def notification_router(service: Callable[[], NotificationService]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/announcements/settings")
    def settings() -> AnnouncementSettings:
        return service().announcements.settings()

    @router.patch("/announcements/settings")
    def configure(value: AnnouncementSettings) -> AnnouncementSettings:
        return service().announcements.configure(value, partial=True)

    @router.post("/announcements/poll")
    async def poll(value: Presence, request: Request) -> Delivery:
        # The shared event stream paces presence. Never occupy an HTTP connection
        # while idle: several browser tabs otherwise starve ordinary reads.
        # Tabs loaded before the upgrade still immediately repeat their polls.
        # Keep their pacing until reload, without slowing the current client.
        if request.headers.get("x-flowfield-event-pacing") != "1":
            await asyncio.sleep(2)
        return await asyncio.to_thread(service().announcements.poll, value)

    @router.get("/updates")
    def updates() -> UpdateStatus:
        return service().updates.status()

    @router.post("/updates/check")
    async def check(value: UpdateCheck) -> UpdateStatus:
        return service().updates.trigger(value.reason, value.client_id)

    @router.put("/updates/settings")
    def update_settings(value: UpdateSettings) -> UpdateStatus:
        return service().updates.configure(value.automatic)

    return router
