"""Native text projection preserves content, validation and accurate wire attribution."""

import asyncio
import json

from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ImageContent,
    ListToolsRequest,
    TextContent,
    Tool,
)

from flowfield.adapters.agent_mcp import native_result
from flowfield.agent_tools import ScopedTools


def test_projection_removes_only_exact_duplicate_and_preserves_metadata():
    payload = {"text": "José\n🦉", "next_request": {"revision": 7, "offset": 4000}}
    content = TextContent(type="text", text=json.dumps(payload), annotations={"priority": 1})
    original = CallToolResult(
        content=[content], structuredContent=payload, isError=True, _meta={"receipt": "same"}
    )
    projected = native_result(original)
    assert projected.structuredContent is None
    assert projected.content == original.content
    assert projected.isError and projected.meta == original.meta
    assert original.structuredContent == payload
    for update in (
        {"content": [TextContent(type="text", text="summary")]},
        {"content": [content, TextContent(type="text", text="extra")]},
        {"content": [ImageContent(type="image", data="aQ==", mimeType="image/png")]},
        {"structuredContent": {"additional": "information"}},
        {"structuredContent": None},
    ):
        distinct = original.model_copy(update=update)
        assert native_result(distinct) is distinct
    typed = CallToolResult(
        content=[TextContent(type="text", text='{"value": 0}')],
        structuredContent={"value": False},
    )
    assert native_result(typed) is typed


def test_native_projection_keeps_schemas_input_validation_and_wire_measurements():
    tool = Tool(
        name="read",
        inputSchema={"type": "object", "additionalProperties": False},
        outputSchema={"type": "object", "required": ["value"]},
    )
    payload = {"value": "unchanged"}
    seen = []

    async def call(name, arguments):
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))], structuredContent=payload
        )

    grant = ScopedTools([tool], call)
    grant.result_projection = native_result
    grant._measure = lambda name, arguments, result: seen.append(result)

    async def exercise():
        listed = await grant.server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
        assert listed.root.tools[0].outputSchema == tool.outputSchema
        assert tool.outputSchema is not None

        async def invoke(arguments):
            return (
                await grant.server.request_handlers[CallToolRequest](
                    CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(name="read", arguments=arguments),
                    )
                )
            ).root

        good = await invoke({})
        assert not good.isError and good.structuredContent == payload
        assert json.loads(good.content[0].text) == payload
        invalid = await invoke({"unknown": True})
        assert invalid.isError
        tool.outputSchema = None
        projected = await invoke({})
        assert projected.structuredContent is None
        assert projected.content == good.content
        assert seen == [good, invalid, projected]
        # Internal callers retain the canonical structured contract even on this grant.
        direct = await grant.call("read", {})
        assert direct.structuredContent == payload

    asyncio.run(exercise())
