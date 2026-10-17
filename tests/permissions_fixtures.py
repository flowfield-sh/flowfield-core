"""Reusable isolated setup and scripted peers; no collected test cases."""

import asyncio

from flowfield.permission_models import PermissionOption

OPTIONS = [
    PermissionOption(id="yes", label="Allow once", kind="allow_once"),
    PermissionOption(id="no", label="Reject once", kind="reject_once"),
]


async def pending(owner, *, workspace_scope=False):
    async with asyncio.timeout(4):
        while not (items := owner.page("harbor", workspace_scope=workspace_scope).pending):
            await asyncio.sleep(0.01)
        return items[0]
