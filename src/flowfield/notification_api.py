"""Service lifetime, contextual delivery and independent update controls."""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
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
    def __init__(self, workspace: Workspace):
        self.announcements = Announcements(workspace)
        self.updates = Updates(workspace)
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
        try:
            if self.task:
                await self.task
        finally:
            await self.updates.close()


def notification_router(service: Callable[[], NotificationService]) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/announcements/settings")
    def settings() -> AnnouncementSettings:
        return service().announcements.settings()

    @router.patch("/announcements/settings")
    def configure(value: AnnouncementSettings) -> AnnouncementSettings:
        return service().announcements.configure(value, partial=True)

    @router.post("/announcements/poll")
    async def poll(value: Presence) -> Delivery:
        # Pace on the service: inactive browsers throttle JavaScript timers. Each
        # response drives the next bounded request without a background timer loop.
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
