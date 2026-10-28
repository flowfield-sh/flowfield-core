"""Reusable isolated test support; importing this module never collects a test suite."""

import json
import os
from pathlib import Path
from types import SimpleNamespace


class FakeWorker:
    supports_activity = True
    binary = Path("/test/codex/bin/codex")

    def __init__(self, directory, cwd, environment, *, registration=None):
        self.launch = None  # Deterministic stand-in has no observed native executable.
        self.cwd = cwd
        self.cleanup_confirmed = True
        self.on_activity = None
        self.process = None
        self.on_tool = None
        self.on_usage = None
        self.on_commands = None
        self.stopping = False

    async def start(self, servers):
        from contextlib import AsyncExitStack

        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        self.stack = AsyncExitStack()
        server = servers[0]
        client = await self.stack.enter_async_context(
            httpx.AsyncClient(headers={h.name: h.value for h in server.headers}, trust_env=False)
        )
        read, write, _ = await self.stack.enter_async_context(
            streamable_http_client(server.url, http_client=client)
        )
        session = await self.stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        async def call(name, arguments):
            result = await session.call_tool(name, arguments)
            assert not result.isError, result
            return result.content[0].text

        self.on_tool = call
        self.tools = [{"name": tool.name} for tool in (await session.list_tools()).tools]
        self.session_id = "fixture-" + self.cwd.parent.name
        self.process = SimpleNamespace(pid=os.getpid())

    async def configure(self, choice):
        self.choice = choice
        return choice

    async def prompt(self, text, on_permission, *, attachments=None):
        assert not attachments
        return await self.run(self.choice.model, self.choice.effort, text, self.tools)

    async def run(self, model, effort, prompt, tools):
        brief = json.loads(prompt)
        assert brief["task_key"] in brief["task"]
        assert brief["flowfield_connection"] in brief["instructions"]
        assert "Before ending, call submit_result" in brief["instructions"]
        assert "A final chat summary does not submit a result" in brief["instructions"]
        assert {t["name"] for t in tools} == {
            "read_context",
            "read_evidence",
            "search_context",
            "run_project_checks",
            "ask_question",
            "submit_result",
        }
        assert model == "test-model" and effort == "low"
        (self.cwd / "result.txt").write_text("implemented")
        await self.on_tool(
            "submit_result",
            {"outcome": "complete", "summary": "Implemented", "checks": "Fake fixture checks"},
        )
        return {"status": "completed"}

    async def stop(self):
        self.stopping = True
        return True

    async def close(self):
        if hasattr(self, "stack"):
            await self.stack.aclose()
