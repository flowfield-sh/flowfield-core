"""Live evidence races and focused verification retain exact candidate authority."""

import asyncio
import json

import pytest
from replies_fixtures import historical_note
from result_fixtures import current
from review_execution_fixtures import Reviewer, install, round_trip
from task_reviews_fixtures import configured, verdict

from flowfield.errors import ApplicationError
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig
from flowfield.outputs import Outputs
from flowfield.review_tools import ReviewBridge


def test_historical_notes_are_readable_without_a_new_review_round(tmp_path):
    service, _, run, _, reviews = configured(tmp_path)
    result = current(service)
    note = historical_note(service.workspace, run.task_id, "Earlier testing notes")
    review = reviews.prepare("harbor", result.id)
    review = reviews.claim("harbor", review.id, review.revision)
    review = reviews.begin_attempt("harbor", review.id)
    review = reviews.started("harbor", review.id, review.revision)
    scope = ReviewBridge(reviews, review, review.attempt_id, "reviewer").scope()

    async def read():
        value = await scope.call("read_context", {"section": "observation:" + note.id})
        assert not value.isError and "Earlier testing notes" in str(value)
        assert not (await scope.call("submit_review", verdict().model_dump())).isError

    asyncio.run(read())
    final = reviews.finish_attempt("harbor", review.id, review.attempt_id, cleanup_confirmed=True)
    assert final.verdict.action == "approve" and not current(service).approved_at


@pytest.mark.parametrize("edit_code", [False, True])
def test_verification_uses_generated_launcher_and_retains_evidence_without_new_candidate(
    tmp_path, monkeypatch, edit_code
):
    service, _, run, _, reviews = configured(tmp_path)
    result = current(service)
    inspection = Inspections(service.workspace)
    settings = inspection.settings(run.project_id)
    inspection.configure(
        run.project_id,
        InspectionConfig(
            expected_revision=settings.revision, run_command="printf 'launcher verified\\n'"
        ),
    )
    install(monkeypatch, "request_verification")
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.recipient == "implementer" and review.verification

    class Verifier(Reviewer):
        async def prompt(self, text, on_permission, *, attachments=None):
            from permissions_fixtures import pending

            from flowfield.adapters.agent_contract import PermissionRequest
            from flowfield.permission_models import PermissionAnswer

            request = asyncio.create_task(
                on_permission(
                    PermissionRequest(
                        "shell",
                        "Execute generated launcher",
                        (("yes", "Allow once", "allow_once"), ("no", "Reject", "reject_once")),
                    )
                )
            )
            record = await pending(service.permissions)
            assert record.review_attempt_id and record.run_id is None
            service.permissions.answer(
                "harbor",
                record.id,
                PermissionAnswer(expected_revision=record.revision, option_id="yes"),
            )
            assert await request == "yes"
            return await super().prompt(text, on_permission, attachments=attachments)

        async def run(self, model, effort, prompt, tools):
            brief = json.loads(prompt)
            assert self.output_directory == self.cwd.parent / "runtime/output"
            assert brief["output_directory"] == str(self.output_directory)
            assert brief["runtime_directory"] == str(self.output_directory.parent)
            assert brief["candidate_commit"] == result.candidate_commit
            process = await asyncio.create_subprocess_shell(
                brief["result_launcher"], stdout=asyncio.subprocess.PIPE
            )
            output, _ = await process.communicate()
            assert process.returncode == 0 and output == b"launcher verified\n"
            target = self.cwd.parent / "runtime/output/proof.txt"
            target.write_bytes(output)
            if edit_code:
                (self.cwd / "result.txt").write_text("unauthorized edit")
            await self.on_tool(
                "answer_review",
                {
                    "action": "answer",
                    "summary": "Launcher works",
                    "evidence": "Executed generated launcher",
                    "files": [{"path": "proof.txt"}],
                },
            )
            return {"status": "completed"}

    monkeypatch.setattr(
        "flowfield.native_review_execution.create",
        lambda choice, directory, cwd, environment, **kw: Verifier(
            directory, cwd, environment, **kw
        ),
    )
    asyncio.run(round_trip(service))
    review = reviews.latest("harbor", run.task_id)
    assert review.status == ("failed" if edit_code else "queued")
    attempt = reviews.attempts("harbor", review.id)["items"][0]
    assert attempt["launch_reason"] == "result-verification"
    assert attempt["cleanup_confirmed"]
    file = Outputs(service.workspace).get("harbor", attempt["output_ids"][0])
    assert file.review_attempt_id == attempt["id"] and file.run_id is None
    assert file.result_id == result.id
    assert Outputs(service.workspace).content(file).read_bytes() == b"launcher verified\n"
    assert (
        current(service).id == result.id
        and current(service).candidate_commit == result.candidate_commit
    )
    assert current(service).integration_id == result.integration_id
    assert not current(service).approved_at
    if not edit_code:
        install(monkeypatch)
        asyncio.run(round_trip(service))
        assert reviews.latest("harbor", run.task_id).verdict.action == "approve"


def test_observation_attachment_requires_sent_result_binding(tmp_path):
    import base64

    from flowfield.attachments import Attachments, AttachmentUpload
    from flowfield.task_evidence import read_file

    service, _, run, _, _ = configured(tmp_path)
    result = current(service)
    attachment = Attachments(service.workspace).upload(
        "harbor",
        AttachmentUpload(
            task_id=run.task_id,
            name="observed.txt",
            mime="text/plain",
            data=base64.b64encode(b"Observed behavior").decode(),
        ),
    )
    root = tmp_path / "review/evidence"
    with pytest.raises(ApplicationError, match="outside"):
        read_file(
            service.workspace,
            "harbor",
            run.task_id,
            attachment.id,
            root,
            result_before=result.version + 1,
        )
    historical_note(service.workspace, run.task_id, f"Observed behavior: [file]({attachment.href})")
    saved = json.loads(
        read_file(
            service.workspace,
            "harbor",
            run.task_id,
            attachment.id,
            root,
            result_before=result.version + 1,
        )
    )
    from pathlib import Path

    assert Path(saved["path"]).read_bytes() == b"Observed behavior"
    with pytest.raises(ApplicationError, match="outside"):
        read_file(
            service.workspace,
            "harbor",
            run.task_id,
            attachment.id,
            root,
            result_before=result.version,
        )
