"""MCP metrics reuse HTTP read ownership and server-bound Coordinator scope."""

import asyncio

from usage_fixtures import accounting, event
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_tools import workspace_scope
from flowfield.cost_models import PricingEvidence
from flowfield.mcp import create_mcp
from flowfield.supervisor import Supervisor
from flowfield.workspaces import WorkspaceCreate, Workspaces


def test_shared_and_scoped_metrics_parity_and_isolation(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(
            run,
            pricing=PricingEvidence(
                model="claude-sonnet-5-5",
                provider="anthropic",
                cache_write_short=10,
                cache_write_long=0,
            ),
        ),
    )
    store = execution.workspace
    other = Workspaces(store).create(WorkspaceCreate(name="Other", **TEST_AGENTS))

    async def exercise():
        service = Supervisor(store)
        canonical = create_mcp(lambda: store, lambda: service)
        grant = await workspace_scope(service, "harbor")
        for name, args in [
            ("get_workspace_metrics", {}),
            ("get_task_metrics", {"task_id": run.task_id}),
            ("get_metrics_evidence", {"source": "run", "source_id": run.id, "limit": 1}),
        ]:
            assert grant.tools[name].annotations.readOnlyHint
            assert "workspace_id" not in grant.tools[name].inputSchema["properties"]
            shared = await canonical.call_tool(name, {"workspace_id": "harbor", **args})
            scoped = await grant.call(name, args)
            assert not shared.isError and not scoped.isError
            a, b = shared.structuredContent, scoped.structuredContent
            assert a["cost_usd"] == b["known_cost_usd"] == "0.00043"
            assert "buckets" not in a and "series" not in b
            assert (a.get("totals") or a.get("execution"))["cost"]["nanodollars"] == 430000
            assert (a.get("totals") or a.get("execution"))["cost"] == (
                b.get("totals") or b.get("execution")
            )["cost"]
            denied = await grant.call(name, {"workspace_id": other.id, **args})
            assert denied.isError
        assert (
            await canonical.call_tool(
                "get_task_metrics", {"workspace_id": other.id, "task_id": run.task_id}
            )
        ).isError
        assert (
            await canonical.call_tool(
                "get_metrics_evidence",
                {"workspace_id": other.id, "source": "run", "source_id": run.id},
            )
        ).isError
        invalid = await grant.call("get_workspace_metrics", {"filters": {"limit": 10001}})
        assert invalid.isError
        charts = await grant.call("get_workspace_metrics", {"include_timeline": True})
        assert charts.structuredContent["buckets"]
        assert charts.structuredContent["series"]
        grant.revoke()
        assert (await grant.call("get_workspace_metrics", {})).isError

    asyncio.run(exercise())
