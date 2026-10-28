"""Paused persisted UI fixture with synthetic reports for all three adapter contracts.

No native process or provider is invoked. Use seed(state) from an owned disposable
service/preview wrapper; it returns exact task/result IDs and the fixed period.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from lifecycle_fixtures import run_report
from workspace_fixtures import TEST_AGENTS

from flowfield.agent_models import AgentChoice
from flowfield.application import TaskCreate, Workspace
from flowfield.execution import Execution
from flowfield.results import Results
from flowfield.usage import Usage
from flowfield.usage_models import Consumption, Tokens
from flowfield.workspaces import WorkspaceCreate, Workspaces


def seed(state: Path):
    store = Workspace(state)
    owner = Workspaces(store).create(WorkspaceCreate(name="Usage preview", **TEST_AGENTS)).id
    task = store.create_task(
        owner,
        TaskCreate(
            title="Trace recorded task usage",
            body="Deterministic preview: 3,800 reported tokens across Claude Code, Codex and Pi. "
            "Synthetic numeric reports exercise persisted accounting and exact result links; "
            "they do not establish live-provider completeness.",
        ),
    )
    execution = Execution(store)
    usage = Usage(store)
    runs = []
    for index, (harness, total) in enumerate([("claude-code", 2000), ("codex", 1200), ("pi", 600)]):
        # The report helper owns queue pausing, publication and result delivery.
        choice = AgentChoice(harness=harness, model="synthetic-requested-model")
        with patch("lifecycle_fixtures.AgentChoice", return_value=choice):
            task = run_report(
                store, owner, task.id, expected_revision=task.revision, status="running"
            )
        run = next(run for run in execution.active() if run.task_id == task.id)
        runs.append(run.id)
        session = f"synthetic-{harness}"
        with patch(
            "flowfield.application.now", return_value=f"2026-10-27T{10 + index}:00:00+00:00"
        ):
            usage.start(owner, "run", run.id, session)
        for observation_index, amount in enumerate([total // 2, total]):
            codex = harness == "codex"
            pi = harness == "pi"
            with patch(
                "flowfield.application.now",
                return_value=f"2026-10-27T{10 + index}:{10 + observation_index * 10}:00+00:00",
            ):
                usage.observe(
                    owner,
                    "run",
                    run.id,
                    Consumption(
                        event_id=f"synthetic-result-{observation_index}",
                        session_id=session,
                        native_scope_id=session if codex else None,
                        dispatch_id="synthetic-dispatch",
                        contract="codex-thread-total/0.164.0"
                        if codex
                        else "pi-session-stats/1"
                        if pi
                        else "claude-model-usage/0.3.293",
                        native_version="0.164.0" if codex else "1.4.0" if pi else "2.1.293",
                        adapter_version="codex-app-server-v1"
                        if codex
                        else "pi-rpc-v1"
                        if pi
                        else "claude-sdk-v1",
                        scope="native-thread"
                        if codex
                        else "native-session-all-entries"
                        if pi
                        else "query-pipeline-including-native-children",
                        model=None if codex or pi else "synthetic-reported-model",
                        counters=Tokens(
                            input=amount * 9 // 10,
                            output=amount // 10,
                            cache_read=0,
                            cache_write=0,
                            reasoning=0 if codex else None,
                            total=amount,
                        ),
                        semantics="inclusive-cache" if codex else "disjoint-cache",
                        uncertain_components=["cache_read", "cache_write", "reasoning"]
                        if codex or pi
                        else [],
                        limitations=["native-provider-coverage-unknown"] if pi else [],
                        final=observation_index == 1,
                        order=observation_index,
                    ),
                )
        with patch(
            "flowfield.application.now", return_value=f"2026-10-27T{10 + index}:20:00+00:00"
        ):
            usage.end(owner, "run", run.id, confirmed=True)
        task = run_report(store, owner, task.id, expected_revision=task.revision, status="done")
    result = Results(store).page(owner, task.id).items[0]
    return {
        "url": f"/workspaces/{owner}/tasks/{task.key}",
        "workspace": owner,
        "task": task.id,
        "runs": runs,
        "result": result.id,
        "start": "2026-10-27T00:00:00Z",
        "end": "2026-10-28T00:00:00Z",
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]))))
