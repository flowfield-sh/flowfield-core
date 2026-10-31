"""Exact ranges remain scoped and navigable without confusing excerpts with whole sources."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from execution_fixtures import BASE, fixture

from flowfield.context_pages import ReadContext
from flowfield.documents import DocumentCreate, DocumentEdit, Documents
from flowfield.errors import ApplicationError
from flowfield.mcp import create_mcp
from flowfield.source_navigation import navigate
from flowfield.worker_tools import WorkerBridge


def test_outline_ranges_unicode_fences_and_search_continuation():
    text = (
        "# Start\n🦉 intro\n```md\n# Not a heading\n```\n## Repeat\nneedle\n# Repeat\n"
        + "needle\n" * 12
    )
    outline = navigate(text, mode="outline")
    assert [h["title"] for h in outline["headings"]] == ["Start", "Repeat", "Repeat"]
    section = outline["headings"][1]
    read = navigate(text, offset=section["offset"], end=section["end_offset"])
    assert read["text"] == "## Repeat\nneedle\n"
    assert read["sha256"] == outline["sha256"] and read["next_offset"] is None
    first = navigate(text, mode="search", query="NEEDLE")
    rest = navigate(text, mode="search", query="NEEDLE", offset=first["next_offset"])
    hits = first["matches"] + rest["matches"]
    assert len(hits) == 13 and len({h["offset"] for h in hits}) == 13
    assert all(text[h["offset"] : h["end_offset"]] == "needle" for h in hits)
    assert rest["next_offset"] is None
    pages = []
    offset = 0
    while True:
        page = navigate(text, limit=3, offset=offset)
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(pages) == text
    with pytest.raises(ApplicationError):
        navigate(text, offset=3, end=2)


def test_outline_pagination_and_literal_search():
    text = "".join(f"# Heading {n}\n" for n in range(41))
    first = navigate(text, mode="outline")
    second = navigate(text, mode="outline", offset=first["next_offset"])
    third = navigate(text, mode="outline", offset=second["next_offset"])
    assert len(first["headings"] + second["headings"] + third["headings"]) == 41
    assert third["next_offset"] is None
    assert navigate(text, mode="search", query=".*")["matches"] == []
    assert navigate("", mode="read")["next_offset"] is None


def test_document_navigation_pins_revision_and_mcp_scope(tmp_path):
    store = fixture(tmp_path).workspace
    docs = Documents(store)
    doc = docs.create("harbor", DocumentCreate(title="Guide", body="# Original\n🦉 required rule"))
    docs.edit("harbor", doc.id, DocumentEdit(expected_revision=1, body="# New\nOther rule"))
    old = docs.text("harbor", doc.id, 1, mode="search", query="required")
    assert len(old["matches"]) == 1 and old["revision"] == 1
    assert docs.text("harbor", doc.id, 2, mode="search", query="required")["matches"] == []
    with pytest.raises(ApplicationError):
        docs.text("missing", doc.id, 1)

    async def check():
        mcp = create_mcp(lambda: store)
        result = await mcp.call_tool(
            "get_document_text",
            {"workspace_id": "harbor", "document_id": doc.id, "revision": 1, "mode": "outline"},
        )
        # FastMCP preserves the structured result and its text counterpart.
        if isinstance(result, dict):
            data = result["structuredContent"]
        else:
            data = result.structuredContent
        assert data["headings"][0]["title"] == "Original"

    asyncio.run(check())


def test_worker_navigation_cannot_read_unselected_sources(tmp_path):
    execution = fixture(tmp_path)
    run = execution.claim("harbor", BASE, {BASE: set()})
    execution.started("harbor", run.id)

    async def check():
        bridge = WorkerBridge(execution, run, SimpleNamespace())
        source = execution.context_section("harbor", run.id, "description")
        read = json.loads(await bridge.call("read_context", {"section": "description", "limit": 4}))
        assert read["text"] == source[:4]
        with pytest.raises(ApplicationError):
            await bridge.call(
                "read_context",
                {"section": "document:unselected", "mode": "search", "query": "secret"},
            )

    asyncio.run(check())
    assert ReadContext(section="description", mode="outline").page("## A\n")


def test_library_search_cursor_and_guide_progressive_disclosure(tmp_path):
    from flowfield.coordinator import GUIDANCE
    from flowfield.guidance import SKILL_TEMPLATE, template

    store = fixture(tmp_path).workspace
    docs = Documents(store)
    for i in range(7):
        docs.create("harbor", DocumentCreate(title=f"Guide {i}", body="# Policy\nPrecise needle"))
    after = None
    found = []
    while True:
        page = docs.search("harbor", "needle", after=after, limit=2)
        found.extend(page["items"])
        after = page["next_after"]
        if after is None:
            break
    assert len(found) == 7
    for hit in found:
        assert (
            docs.text(
                "harbor",
                hit["id"],
                hit["revision"],
                offset=hit["matches"][0]["offset"],
                end=hit["matches"][0]["end_offset"],
            )["text"]
            == "needle"
        )
    # Meaningful reduction without relying on lossless native compaction/history.
    full = template(SKILL_TEMPLATE).split("---", 2)[2].strip()
    assert len(GUIDANCE.encode()) < len(full.encode()) * 0.4
    assert "explicit human approval of the exact code candidate" in GUIDANCE
    assert "paused queue" in GUIDANCE and "Read required guidance fully" in GUIDANCE
    assert "get_coordinator_guide" in GUIDANCE

    async def read_guide():
        mcp = create_mcp(lambda: store)
        result = await mcp.call_tool("get_coordinator_guide", {"workspace_id": "harbor"})
        data = result["structuredContent"] if isinstance(result, dict) else result.structuredContent
        assert data["headings"] and data["sha256"] == navigate(full)["sha256"]

    asyncio.run(read_guide())
