"""Seed/append deterministic active output for browser tests; never launch a harness."""

import json
import sys
from pathlib import Path

from project_fixtures import task_request

from flowfield.activity import ActivityCreate
from flowfield.agent_models import AgentChoice
from flowfield.application import ProjectSetup, TaskPublish, Workspace
from flowfield.execution import Execution
from flowfield.execution_models import QueueEdit, SettingsEdit, Usage
from flowfield.run_activity import ActivityUpdate, ContextUsage, RunActivity

ws = Workspace(Path(sys.argv[1]))
execution = Execution(ws)
if sys.argv[2] == "create":
    path = Path(sys.argv[1]) / "stream-project"
    path.mkdir()
    ws.setup_project(ProjectSetup(path=str(path), task_prefix="STR"))
    task = ws.create_task(
        "stream-project",
        task_request(
            id="stream",
            title="Observe live output",
            body="Verify the public activity feed.",
            status="up_next",
        ),
    )
    ws.publish_task(
        "stream-project",
        task.id,
        TaskPublish(
            completion="report",
            expected_revision=task.revision,
        ),
    )
    execution.configure(
        "stream-project",
        SettingsEdit(
            expected_revision=1, selection=AgentChoice(model="offline-fixture", effort="none")
        ),
    )
    execution.queue("stream-project", QueueEdit(expected_revision=2, enabled=True))
    run = execution.claim("stream-project", "a" * 40, {})
    execution.started("stream-project", run.id)
    execution.queue("stream-project", QueueEdit(expected_revision=3, enabled=False))
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [
            ActivityUpdate(
                key="context", kind="status", text="", context=ContextUsage(used=12345, size=100000)
            )
        ],
    )
else:
    run = execution.page("stream-project").items[0]
if sys.argv[2] == "later":
    ws.add_activity(
        "stream-project",
        ActivityCreate(task_id="stream", body="Later progress while work continues."),
    )
if sys.argv[2] == "long":
    # Enough independent entries to exercise scrolling after per-entry abbreviation.
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [
            ActivityUpdate(
                key=f"long-{index}",
                kind="output",
                text=f"Saved entry {index}\n" + "Output line\n" * 100,
            )
            for index in range(20)
        ],
    )
if sys.argv[2] == "compact":
    RunActivity(ws).write(
        "stream-project",
        run.id,
        [
            ActivityUpdate(
                key="script",
                kind="command",
                text="python - <<'PY'\n" + "print('retained source')\n" * 100 + "PY\nExit code: 17",
            ),
            ActivityUpdate(
                key="code",
                kind="agent",
                text="Explanation\n```python\nprint('code block source')\n```\nA useful limitation",
            ),
        ],
    )
    execution.usage("stream-project", run.id, Usage(total_tokens=240, cached_input_tokens=100))
elif sys.argv[2] == "stop-race":
    execution.usage("stream-project", run.id, Usage(total_tokens=400, cached_input_tokens=200))
RunActivity(ws).write(
    "stream-project",
    run.id,
    [
        ActivityUpdate(
            key=sys.argv[2],
            kind="output",
            text=sys.argv[2]
            + " observed output"
            + ("\nOutput line" * 100 if sys.argv[2] == "long" else ""),
        )
    ],
)
print(json.dumps({"run_id": run.id}))
