"""Reusable isolated setup and scripted peers; no collected test cases."""

import json

from native_adapters_fixtures import start

from flowfield.adapters.agent_mcp import serve_scope


async def journey(tmp_path, grant, calls):
    events = []
    async with serve_scope(grant) as server:
        client = await start(tmp_path, events, servers=[server])
        try:
            assert (await client.prompt(json.dumps({"calls": calls}), None))[
                "status"
            ] == "completed"
        finally:
            assert await client.stop()
    assert grant.revoked
    return [
        json.loads(event.text)
        for event in events
        if event.kind == "agent" and event.text.startswith("{")
    ]
