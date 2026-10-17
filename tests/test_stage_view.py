"""Stage history describes exact changes; it cannot advance execution or delivery."""

import pytest
from conversation_fixtures import plan
from execution_fixtures import fixture

from flowfield.errors import ApplicationError
from flowfield.stage_models import Stage, StagePlan
from flowfield.stage_view import stage_changes
from flowfield.stages import Stages
from flowfield.thread_view import ThreadView


def test_stage_history_stays_exact_after_progress_and_repeated_saves(tmp_path):
    workspace = fixture(tmp_path, stages=plan().stages).workspace
    stages = Stages(workspace)
    first = stages.get("harbor", "task-0")
    thread = ThreadView(workspace)
    original = thread.item_view("harbor", "task-0", "plan:1")
    assert original.title == "Stages defined"
    assert original.stages == first.stages
    update = plan(1, status="active")
    current = stages.update("harbor", "task-0", update)
    message = thread.item_view("harbor", "task-0", "plan:2")
    assert message.title == "Stage changed"
    assert message.stage_changes == ["Implement: Planned → Active."]
    assert message.stages == current.stages
    assert thread.item_view("harbor", "task-0", "plan:1") == original
    assert (
        stages.update("harbor", "task-0", update.model_copy(update={"expected_revision": 2}))
        == current
    )
    with pytest.raises(ApplicationError):
        stages.update("harbor", "task-0", update)
    assert stages.get("harbor", "task-0") == current
    evidence = update.model_copy(update={"expected_revision": 2, "reason": "A meaningful finding"})
    stages.update("harbor", "task-0", evidence)
    unchanged = thread.item_view("harbor", "task-0", "plan:3")
    assert unchanged.title == "Stage update"
    assert unchanged.stage_changes == ["Stages unchanged."]


@pytest.mark.parametrize(
    "titles", [("Explore", "Implement", "Verify"), ("Investigate", "Synthesize", "Report")]
)
def test_structural_and_scope_changes_are_explicit(titles):
    previous = StagePlan(
        project_id="p",
        task_id="t",
        agreement_revision=1,
        stages=[Stage(id=str(i), title=t, outcome=t) for i, t in enumerate(titles)],
    )
    current = previous.model_copy(deep=True)
    current.agreement_revision = 2
    current.stages = [
        current.stages[2],
        current.stages[0],
        Stage(id="new", title="Summarize", outcome="Report findings"),
    ]
    current.stages[1].title = "Understand"
    current.stages[1].outcome = "Revised requirement"
    title, changes = stage_changes(current, previous)
    assert title == "Stages revised"
    assert changes == [
        f"Renamed {titles[0]} to Understand.",
        "Revised the outcome of Understand.",
        "Added Summarize.",
        f"Removed {titles[1]}.",
        "Reordered stages.",
        "Reconciled stages with the revised task agreement.",
    ]
    assert previous.stages[0].title == titles[0]
