"""Reusable isolated setup and scripted peers; no collected test cases."""

from flowfield.stage_models import Stage, StageUpdate


def plan(revision=0, agreement=1, status="planned"):
    return StageUpdate(
        expected_revision=revision,
        agreement_revision=agreement,
        stages=[
            Stage(
                id="implement",
                title="Implement",
                outcome="Deliver the requested behavior",
                status=status,
            ),
            Stage(id="verify", title="Verify", outcome="Demonstrate the agreed outcome"),
        ],
        reason="Initial sequence" if revision == 0 else "Implementation started",
    )
