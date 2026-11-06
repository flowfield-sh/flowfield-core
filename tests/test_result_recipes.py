"""Exact recipe binding, real repeat verification, artifacts and independent runtime ownership."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

from result_fixtures import current, fixture

from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.output_models import OutputSelection
from flowfield.outputs import Outputs
from flowfield.runtime_models import ResultRecipe


def test_recipe_is_frozen_and_verified_before_human_review(tmp_path):
    recipe = ResultRecipe(
        run_command='printf "hello" > "$FLOWFIELD_OUTPUT_DIR/report.txt"',
        outputs=[OutputSelection(path="report.txt")],
    )
    service, _, _ = fixture(tmp_path, recipe=recipe)
    before = current(service)
    assert before.recipe.run_command == recipe.run_command
    Inspections(service.workspace).configure(
        "harbor", InspectionConfig(expected_revision=2, run_command="exit 17")
    )
    service.results.process("harbor")
    result = current(service)
    assert result.status == "ready", result.problem
    evidence = service.integrations.get("harbor", result.integration_id).runtime_verification
    assert evidence.passed and evidence.cleanup_confirmed
    assert evidence.candidate_commit == result.candidate_commit
    assert evidence.recipe_digest == result.recipe.digest()
    assert [s.name for s in evidence.steps] == [
        "Run 1: completed",
        "Run 1: cleanup",
        "Run 2: completed",
        "Run 2: cleanup",
    ]
    copy = Inspections(service.workspace).prepare(
        "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
    )
    assert copy.run_command == recipe.run_command
    assert copy.workspace != evidence.workspace
    assert not (Path(copy.workspace).parent / "runtime/output/report.txt").exists()


def test_repeat_failure_blocks_review_with_retained_evidence(tmp_path):
    service, _, _ = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command='test ! -f "$FLOWFIELD_RUNTIME_DIR/once" '
            '&& touch "$FLOWFIELD_RUNTIME_DIR/once"'
        ),
    )
    service.results.process("harbor")
    result = current(service)
    assert result.status == "blocked"
    assert result.problem_code == "runtime_verification_failed"
    assert "run 2 failed" in result.problem
    evidence = service.integrations.get("harbor", result.integration_id).runtime_verification
    assert evidence.cleanup_confirmed and not evidence.passed
    assert result.next_action.action == "correct"


def test_generated_files_are_immutable_run_outputs_not_delivery_files(tmp_path):
    service, _, run = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command='echo run >> "$FLOWFIELD_OUTPUT_DIR/report.txt"',
            outputs=[OutputSelection(path="report.txt")],
        ),
    )
    service.results.process("harbor")
    result = current(service)
    assert result.status == "ready", result.problem

    async def journey():
        runtime = service.result_runtimes
        value = await runtime.prepare(
            "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
        )

        async def wait(state):
            async with asyncio.timeout(10):
                while True:
                    current_copy = runtime.copies.get("harbor", value.id)
                    if current_copy.runtime_state == state:
                        return current_copy
                    await asyncio.sleep(0.02)

        try:
            await wait("ready")
            await runtime.run("harbor", value.id)
            first = await wait("finished")
            assert not first.artifact_problem
            assert len(first.files) == 1
            old = Outputs(service.workspace)._path(first.files[0])
            assert old.read_text() == "run\n"
            await runtime.run("harbor", value.id)
            second = await wait("finished")
            assert second.files[0].id != first.files[0].id
            assert second.files[0].runtime_generation != first.files[0].runtime_generation
            assert Outputs(service.workspace)._path(second.files[0]).read_text() == "run\nrun\n"
            assert old.read_text() == "run\n"
            with service.workspace.connection() as db:
                assert Outputs.for_run(db, run.id) == []
            assert current(service).files == []
        finally:
            await runtime.close()

    asyncio.run(journey())


def test_service_verification_uses_allocated_port_and_short_temp_path(tmp_path):
    # Exercise owned loopback I/O, without HTTPServer's reverse DNS lookup or
    # urllib's system proxy discovery becoming part of the readiness deadline.
    script = (
        "import http.server,os,socketserver; "
        'assert len(os.environ["TMPDIR"]) < 60; '
        'print("owned-port",os.environ["FLOWFIELD_PORT"],flush=True); '
        "socketserver.TCPServer.allow_reuse_address=True; "
        'socketserver.TCPServer(("127.0.0.1",int(os.environ["FLOWFIELD_PORT"])), '
        "http.server.SimpleHTTPRequestHandler).serve_forever()"
    )
    probe = (
        "import http.client,os; "
        'connection=http.client.HTTPConnection("127.0.0.1",int(os.environ["FLOWFIELD_PORT"])); '
        'connection.request("GET","/"); '
        "response=connection.getresponse(); assert response.status == 200; response.read()"
    )

    def command(code):
        return shlex.quote(sys.executable) + " -c " + shlex.quote(code)

    service, _, _ = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command=command(script),
            ready_command=command(probe),
            url="http://127.0.0.1:${FLOWFIELD_PORT}",
            run_timeout_seconds=5,
        ),
    )
    service.results.process("harbor")
    result = current(service)
    evidence = service.integrations.get("harbor", result.integration_id).runtime_verification
    assert result.status == "ready", evidence.model_dump()
    assert evidence.passed and evidence.cleanup_confirmed
    receipts = list((Path(evidence.workspace).parent / "runtime").glob("owner-*.json"))
    assert receipts and all(json.loads(p.read_text())["cleanup_confirmed"] for p in receipts)


def test_partial_worker_override_survives_submission_and_keeps_frozen_defaults(tmp_path):
    service, _, _ = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command='test -f "$FLOWFIELD_RUNTIME_DIR/installed"; '
            'echo result > "$FLOWFIELD_OUTPUT_DIR/report.txt"',
            outputs=[OutputSelection(path="report.txt")],
        ),
        setup_commands=['touch "$FLOWFIELD_RUNTIME_DIR/installed"'],
        preview=InspectionConfig(expected_revision=2, run_command="exit 12"),
    )
    result = current(service)
    assert result.recipe.setup_commands == ['touch "$FLOWFIELD_RUNTIME_DIR/installed"']
    assert result.recipe.outputs == [OutputSelection(path="report.txt")]
    service.results.process("harbor")
    assert current(service).status == "ready", current(service).problem


def test_upgrade_freezes_prepared_copy_without_changing_review_or_defaults(tmp_path):
    from flowfield.runtime_schema import migrate

    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    copy = Inspections(service.workspace).prepare(
        "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
    )
    copy.run_command = "echo historical"
    copy.ready_command = ""
    Inspections(service.workspace)._save(copy)
    Inspections(service.workspace).configure(
        "harbor", InspectionConfig(expected_revision=2, run_command="echo today")
    )
    with service.workspace.connection(write=True) as db:
        db.execute(
            "UPDATE result_versions SET data=json_remove(data,'$.recipe') WHERE id=?", (result.id,)
        )
        before = json.loads(
            db.execute("SELECT data FROM result_versions WHERE id=?", (result.id,)).fetchone()[0]
        )
        migrate(db)
        after = json.loads(
            db.execute("SELECT data FROM result_versions WHERE id=?", (result.id,)).fetchone()[0]
        )
        assert after.pop("recipe")["run_command"] == "echo historical"
        assert before == after
    assert Inspections(service.workspace).settings("harbor").run_command == "echo today"
    assert not Inspections(service.workspace).get("harbor", copy.id).runtime_prepared


def test_verification_timeout_and_source_changes_are_actionable(tmp_path):
    for name, command, expected in [
        ("timeout", "sleep 30", "timed out"),
        ("source", "echo changed > result.txt", "source files"),
    ]:
        service, _, _ = fixture(
            tmp_path / name, recipe=ResultRecipe(run_command=command, run_timeout_seconds=1)
        )
        service.results.process("harbor")
        result = current(service)
        assert result.status == "blocked" and result.next_action.action == "correct"
        evidence = service.integrations.get("harbor", result.integration_id).runtime_verification
        assert expected in evidence.problem
        assert evidence.cleanup_confirmed


def test_unconfirmed_verification_blocks_correction_until_owner_confirms(tmp_path):
    import pytest

    from flowfield.errors import ApplicationError
    from flowfield.execution_models import RunAction
    from flowfield.runtime_verification import require_cleanup

    service, _, _ = fixture(tmp_path, recipe=ResultRecipe(run_command="exit 8"))
    service.results.process("harbor")
    result = current(service)
    evidence = service.integrations.get("harbor", result.integration_id).runtime_verification
    receipt = next((Path(evidence.workspace).parent / "runtime").glob("owner-*.json"))
    receipt.write_text('{"cleanup_confirmed": false}')
    with pytest.raises(ApplicationError, match="cleanup is unconfirmed"):
        service.results.correct("harbor", result.id, RunAction(expected_revision=result.revision))
    assert current(service).id == result.id
    receipt.write_text('{"cleanup_confirmed": true}')
    require_cleanup(service.workspace, "harbor", "work")


def test_saved_terminal_launcher_has_same_port_and_short_temp_contract(tmp_path):
    import subprocess

    service, _, _ = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command='test ${#TMPDIR} -lt 60 && test "$FLOWFIELD_PORT" -gt 0 '
            '&& printf "%s" "$TMPDIR"'
        ),
    )
    service.results.process("harbor")
    result = current(service)
    assert result.status == "ready", result.problem
    copy = Inspections(service.workspace).prepare(
        "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
    )
    completed = subprocess.run(
        ["/bin/sh", copy.launcher], capture_output=True, text=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("/tmp/ff-r-terminal-")
    assert not Path(completed.stdout).exists()


def test_recorded_short_temp_alias_recovers_only_after_confirmed_cleanup(tmp_path):
    from flowfield.adapters.local_result import LocalResultEnvironment

    service, _, _ = fixture(tmp_path)
    service.results.process("harbor")
    result = current(service)
    copy = Inspections(service.workspace).prepare(
        "harbor", InspectionPrepare(result_id=result.id, expected_revision=result.revision)
    )
    env = LocalResultEnvironment(copy.id, Path(copy.workspace), "recovery", lambda _: None)
    env.port_lease.close()  # Simulate process exit; recovery never signals unrelated owners.
    receipt = env.runtime / "owner-recovery.json"
    receipt.write_text('{"cleanup_confirmed": false}')
    env.recover_temporary(env.runtime)
    assert env.temporary.exists()
    receipt.write_text('{"cleanup_confirmed": true}')
    env.recover_temporary(env.runtime)
    assert not env.temporary.exists()


def test_worker_cannot_silently_remove_the_frozen_run_contract(tmp_path):
    import pytest

    from flowfield.errors import ApplicationError

    with pytest.raises(ApplicationError, match="run command or explain"):
        fixture(
            tmp_path,
            recipe=ResultRecipe(run_command="  "),
            preview=InspectionConfig(expected_revision=2, run_command="echo usable"),
        )


def test_verification_rejects_generated_files_that_cannot_be_retained(tmp_path):
    script = (
        'import os; f=open(os.environ["FLOWFIELD_OUTPUT_DIR"]+"/large.bin","wb"); '
        "f.truncate(128*1024*1024+1); f.close()"
    )
    service, _, _ = fixture(
        tmp_path,
        recipe=ResultRecipe(
            run_command=shlex.quote(sys.executable) + " -c " + shlex.quote(script),
            outputs=[OutputSelection(path="large.bin")],
        ),
    )
    service.results.process("harbor")
    result = current(service)
    assert result.status == "blocked" and "128 MiB" in result.problem
    assert result.next_action.action == "correct"


def test_correction_context_distinguishes_current_defaults_and_previous_effective_recipe(tmp_path):
    from flowfield.worker_context import brief_context, enrich

    old = ResultRecipe(
        run_command="echo previous",
        ready_command="true",
        url="http://localhost:1234",
        outputs=[OutputSelection(path="old.csv")],
    )
    service, _, run = fixture(tmp_path, recipe=old, setup_commands=["echo reusable-setup"])
    Inspections(service.workspace).configure(
        "harbor", InspectionConfig(expected_revision=2, run_command="echo new-default")
    )
    previous = current(service).recipe
    sections = {"predecessor": json.dumps({"recipe": previous.model_dump()})}
    with service.workspace.connection() as db:
        enrich(db, run, sections)
    defaults = json.loads(sections["preview"])
    context = json.loads(sections["recipe_context"])
    assert defaults["run_command"] == "echo new-default"
    assert defaults["setup_commands"] == ["echo reusable-setup"]
    assert defaults["outputs"] == [] and defaults["url"] == ""
    assert context["previous_recipe"] == previous.model_dump()
    assert {"run_command", "ready_command", "url", "outputs"} <= set(context["different_fields"])
    assert "recipe_context" in brief_context(sections)["context"]
    assert "preview" in brief_context(sections)["context"]
    assert current(service).recipe == previous
