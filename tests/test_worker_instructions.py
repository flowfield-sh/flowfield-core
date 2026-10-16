"""An attempt gets applicable obligations without losing scope or completion rules."""

import json

from flowfield.worker_context import brief_context
from flowfield.worker_instructions import instructions


def test_fresh_worker_has_completion_contract_without_continuation_instructions():
    text = instructions({"description": "Do the work"}, server="scope", completion="code")
    assert "submit_result" in text and "ask_question" in text
    assert "Final milestone promotion requires human approval" in text
    assert "separately granted milestone-branch integration policy" in text
    assert "Do not approve or integrate code yourself" in text
    assert "Do not use other Flowfield" in text
    for absent in ("Prior unfinished", "Correction contains", "Human testing", "preceding result"):
        assert absent not in text
    assert "report-only work" not in text


def test_answer_correction_and_review_obligations_follow_frozen_sections():
    sections = {
        name: "evidence"
        for name in (
            "input",
            "earlier_answers",
            "correction",
            "feedback",
            "human_testing",
            "prerequisites",
        )
    }
    text = instructions(sections, server="scope", completion="code")
    for required in (
        "Prior unfinished",
        "Earlier answers",
        "Correction contains",
        "Human testing",
        "preceding result",
        "other_repository",
    ):
        assert required in text
    report = instructions({}, server="scope", completion="report")
    assert "without changing the repository tree" in report
    assert "submit_result" in report


def test_required_sources_precede_large_workflow_evidence_and_references_stay_retrievable():
    sections = {
        "description": "Current agreement",
        "feedback": "",
        "input": "[]",
        "stages": "workflow " * 1000,
        "attempt_history": json.dumps({"items": [], "earlier_omitted": 0}),
        "document:required": "essential " * 600,
        "document:reference": "optional context",
        "documentation": json.dumps(
            {
                "items": [
                    {"id": "reference", "section": "document:reference", "purpose": "reference"},
                    {"id": "required", "section": "document:required"},
                ]
            }
        ),
    }
    brief = brief_context(sections)
    assert brief["context"]["document:required"] == sections["document:required"]
    assert "stages" not in brief["context"] and "document:reference" not in brief["context"]
    assert brief["sections"]["document:reference"] == len("optional context")
    assert "input" not in brief["sections"] and "attempt_history" not in brief["sections"]
    assert len(json.dumps(brief["context"])) <= 10000
