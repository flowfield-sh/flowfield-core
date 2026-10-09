"""Private, short-lived MCP endpoint for a single service-owned agent grant."""

import asyncio
import secrets
import socket
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from uuid import uuid4

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from flowfield.adapters.agent_contract import McpServer
from flowfield.agent_tools import ScopedTools


class ScopeServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The main service owns OS signal handling, not its per-agent endpoints.
        yield


@asynccontextmanager
async def serve_scope(
    grant: ScopedTools, *, lifetime: float = 3600, name: str | None = None
) -> AsyncIterator[McpServer]:
    """No public route, access logs, persisted token or ambient workspace authority.

    Only agents advertising HTTP MCP can use this endpoint. Stdio-only harnesses
    require a separate transport adapter; never silently omit their Flowfield tools.
    """
    token = secrets.token_urlsafe(32)
    name = name or "flowfield_" + uuid4().hex[:16]
    expires = time.monotonic() + lifetime
    manager = StreamableHTTPSessionManager(
        grant.server,
        stateless=True,
        json_response=True,
        max_request_body_size=64 * 1024,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=[],
        ),
    )

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        authorized = secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {token}"
        )
        if (
            grant.revoked
            or time.monotonic() >= expires
            or not authorized
            or scope["path"] != "/mcp"
            or request.headers.get("origin") is not None
        ):
            await Response(status_code=403)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    sock = socket.socket()
    task: asyncio.Task[None] | None = None
    server = ScopeServer(
        uvicorn.Config(
            endpoint,
            host="127.0.0.1",
            port=0,
            lifespan="off",
            access_log=False,
            log_level="error",
            limit_concurrency=16,
            timeout_graceful_shutdown=5,
        )
    )
    try:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        async with manager.run():
            task = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Scoped MCP server did not start")
                    await asyncio.sleep(0.01)
            try:
                yield McpServer.model_validate(
                    {
                        "type": "http",
                        "name": name,
                        "url": f"http://127.0.0.1:{sock.getsockname()[1]}/mcp",
                        "headers": [{"name": "Authorization", "value": f"Bearer {token}"}],
                    }
                )
            finally:
                # Drain requests while the MCP session manager remains alive,
                # including when the agent failed or its owner was cancelled.
                grant.revoke()
                server.should_exit = True
                async with asyncio.timeout(7):
                    await task
    except BaseExceptionGroup as group:
        # The session manager's task group wraps errors from the calling turn.
        # Preserve one original failure (and its actionable application message),
        # without hiding concurrent endpoint/shutdown failures.
        error: BaseException = group
        while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
            error = error.exceptions[0]
        raise error from None
    finally:
        grant.revoke()
        server.should_exit = True
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        sock.close()
