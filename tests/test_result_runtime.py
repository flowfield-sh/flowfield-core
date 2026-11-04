"""Real owned result processes, exact candidates, correction and restart; no models."""

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from result_fixtures import current, fixture

from flowfield.adapters import result_process
from flowfield.errors import ApplicationError
from flowfield.inspection import Inspections
from flowfield.inspection_models import InspectionConfig, InspectionPrepare
from flowfield.result_models import ResultReview
from flowfield.result_runtime import ResultRuntimes
from flowfield.runtime_models import ResultRecipe


async def settled(runtimes, value, states):
    async with asyncio.timeout(15):
        while True:
            saved = runtimes.copies.get(value.workspace_id, value.id)
            if saved.runtime_state in states:
                return saved
            await asyncio.sleep(0.05)


def configured(tmp_path, command="sleep 60", **settings):
    service, repo, run = fixture(tmp_path)
    service.results.process("harbor")
    Inspections(service.workspace).configure(
        "harbor", InspectionConfig(expected_revision=2, run_command=command, **settings)
    )
    # Seed an already-reviewed recipe to isolate the human runtime lifecycle, including
    # deliberately failing launchers. Full pre-review verification is exercised separately.
    with service.workspace.connection(write=True) as db:
        version = service.results._get(db, "harbor", current(service).id)
        version.recipe = ResultRecipe(
            run_command=command,
            ready_command=settings.get("ready_command", ""),
            url=settings.get("url", ""),
        )
        service.results._save(db, version)
    return service, current(service)


async def prepared(service, version):
    value = await service.result_runtimes.prepare(
        "harbor",
        InspectionPrepare(
            result_id=version.id,
            expected_revision=version.revision,
        ),
    )
    return await settled(service.result_runtimes, value, {"ready", "failed", "uncertain"})


def test_prepare_run_stop_and_restart_preserve_candidate(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(configured, tmp_path, "echo started; sleep 60")
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            assert value.runtime_state == "ready", value.runtime_problem
            await runtime.run("harbor", value.id)
            running = await settled(runtime, value, {"running", "failed", "uncertain"})
            assert running.runtime_state == "running", running.runtime_problem
            await runtime.close()
            stopped = runtime.copies.get("harbor", value.id)
            assert stopped.runtime_state == "stopped", stopped.runtime_problem
            assert "started" in stopped.runtime_output
            assert Path(stopped.workspace).exists()
            assert current(service).candidate_commit == version.candidate_commit
            assert not current(service).approved_at
            restarted = ResultRuntimes(service.workspace)
            restarted.recover()
            await restarted.schedule()
            assert not restarted.jobs
            with pytest.raises(ApplicationError, match="Prepare"):
                await restarted.run("harbor", value.id)
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_service_mcp_http_shutdown_and_explicit_restart(tmp_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from service_fixtures import running_service

    pid_file = tmp_path / "owned-pid"
    service, version = configured(
        tmp_path, f"echo $$ > {shlex.quote(str(pid_file))}; echo launched; sleep 60"
    )
    saved = {}

    async def exercise(base):
        async with (
            streamable_http_client(base + "/mcp/") as (read, write, _),
            ClientSession(read, write) as session,
            httpx.AsyncClient(base_url=base, trust_env=False) as api,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"prepare_result_runtime", "run_result", "stop_result"} <= names
            reply = await session.call_tool(
                "prepare_result_runtime",
                {
                    "workspace_id": "harbor",
                    "request": {"result_id": version.id, "expected_revision": version.revision},
                },
            )
            assert not reply.isError, reply
            value = reply.structuredContent
            path = f"/api/workspaces/harbor/inspections/{value['id']}"

            async def wait_state(state):
                async with asyncio.timeout(10):
                    while True:
                        observed = (await api.get(path)).json()
                        if observed["runtime_state"] == state:
                            return observed
                        await asyncio.sleep(0.05)

            await wait_state("ready")
            reply = await session.call_tool(
                "run_result", {"workspace_id": "harbor", "inspection_id": value["id"]}
            )
            assert not reply.isError, reply
            await wait_state("running")
            log = await api.get(path + "/runtime/log")
            assert log.status_code == 200 and "launched" in log.text
            stopped = await api.post(path + "/runtime", json={"action": "stop"})
            assert stopped.json()["runtime_state"] == "stopped"
            assert stopped.json()["runtime_prepared"]
            rerun = await api.post(path + "/runtime", json={"action": "run"})
            assert rerun.status_code == 200, rerun.text
            saved.update(await wait_state("running"))

    with running_service(service.workspace.directory) as base:
        asyncio.run(exercise(base))
        pid = int(pid_file.read_text())
        os.kill(pid, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert Path(saved["workspace"]).is_dir()
    with running_service(service.workspace.directory) as base, httpx.Client(trust_env=False) as api:
        path = f"{base}/api/workspaces/harbor/inspections/{saved['id']}"
        assert api.get(path).json()["runtime_state"] == "stopped"
        assert api.post(path + "/runtime", json={"action": "run"}).status_code == 409
    assert not current(service).approved_at


def test_feedback_stops_old_result_and_rejects_restart(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured, tmp_path, "echo diagnostic-output; sleep 60"
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            await settled(runtime, value, {"running"})
            service.results.review(
                "harbor",
                version.id,
                ResultReview(
                    expected_revision=version.revision,
                    candidate_commit=version.candidate_commit,
                    action="request_changes",
                    note="Make it better",
                ),
            )
            await runtime.schedule()
            assert runtime.copies.get("harbor", value.id).runtime_state == "stopped"
            from flowfield.worker_context import enrich

            sections = {}
            with service.workspace.connection() as db:
                enrich(db, service.execution.get("harbor", version.run_id), sections)
            observation = json.loads(sections["result_runtime"])
            assert observation["result_id"] == version.id
            assert observation["commit"] == version.candidate_commit
            assert observation["state"] == "stopped"
            assert "diagnostic-output" in observation["output"]
            with pytest.raises(ApplicationError, match="current ready"):
                await runtime.run("harbor", value.id)
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_one_runtime_owns_multiple_services_and_preserves_unrelated_process(tmp_path):
    async def exercise():
        pid_file = tmp_path / "children"
        command = (
            "sleep 60 & first=$!; sleep 60 & second=$!; "
            f"printf '%s\\n' $first $second > {shlex.quote(str(pid_file))}; wait"
        )
        service, version = await asyncio.to_thread(configured, tmp_path, command)
        runtime = service.result_runtimes
        unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            await settled(runtime, value, {"running"})
            children = [int(pid) for pid in pid_file.read_text().splitlines()]
            assert len(children) == 2
            for pid in children:
                os.kill(pid, 0)
            await runtime.stop("harbor", value.id)
            for pid in children:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            assert unrelated.poll() is None
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)
            await runtime.close()

    asyncio.run(exercise())


def test_approval_keeps_preview_running_through_delivery(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(configured, tmp_path)
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            await settled(runtime, value, {"running"})
            service.results.review(
                "harbor",
                version.id,
                ResultReview(
                    expected_revision=version.revision,
                    candidate_commit=version.candidate_commit,
                    action="approve",
                ),
            )
            assert current(service).status == "delivering"
            await runtime.schedule()
            assert runtime.copies.get("harbor", value.id).runtime_state == "running"
            await asyncio.to_thread(service.results.process, "harbor")
            assert current(service).status == "delivered"
            await runtime.schedule()
            assert runtime.copies.get("harbor", value.id).runtime_state == "running"
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_failed_run_and_bounded_output(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured, tmp_path, "yes output | head -c 40000; exit 7"
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            failed = await settled(runtime, value, {"failed", "uncertain"})
            assert failed.runtime_state == "failed", failed.runtime_problem
            assert "7" in failed.runtime_problem
            assert failed.runtime_truncated
            assert len(failed.runtime_output.encode()) <= 16000
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_automatic_preparation_once_and_no_restart_autorun(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(configured, tmp_path, auto_prepare=True)
        runtime = service.result_runtimes
        try:
            await runtime.schedule()
            value = runtime.copies.latest("harbor", version.id)
            assert value
            ready = await settled(runtime, value, {"ready"})
            await runtime.schedule()
            assert runtime.copies.latest("harbor", version.id).id == ready.id
            await runtime.close()
            restarted = ResultRuntimes(service.workspace)
            restarted.recover()
            await restarted.schedule()
            assert not restarted.jobs
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_automatic_preparation_waits_for_independent_review(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(configured, tmp_path, auto_prepare=True)
        runtime = service.result_runtimes
        try:
            with service.workspace.connection(write=True, workspace_id="harbor") as db:
                task = service.workspace._task(db, "harbor", version.task_id)
                service.execution._task_state(db, task, "in_progress", "Independent review.")
            await runtime.schedule()
            assert runtime.copies.latest("harbor", version.id) is None
            with service.workspace.connection(write=True, workspace_id="harbor") as db:
                task = service.workspace._task(db, "harbor", version.task_id)
                service.execution._task_state(db, task, "in_review", "Review passed.")
            await runtime.schedule()
            value = runtime.copies.latest("harbor", version.id)
            assert value
            assert (await settled(runtime, value, {"ready"})).runtime_prepared
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_guardian_stops_command_when_service_pipe_disappears(tmp_path):
    receipt = tmp_path / "receipt.json"
    pidfile = tmp_path / "child.pid"
    owner = subprocess.Popen(
        [
            sys.executable,
            result_process.__file__,
            str(receipt),
            f'echo $$ > "{pidfile}"; exec sleep 60',
        ],
        stdin=subprocess.PIPE,
    )
    try:
        import time

        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        child = int(pidfile.read_text())
        owner.stdin.close()
        owner.wait(timeout=8)
        assert json.loads(receipt.read_text())["cleanup_confirmed"]
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        if owner.poll() is None:
            owner.send_signal(signal.SIGTERM)
            owner.wait(timeout=8)


def test_concurrent_run_requests_share_one_process_and_block_deletion(tmp_path):
    from flowfield.task_deletion import TaskDeletionService

    async def exercise():
        service, version = await asyncio.to_thread(
            configured, tmp_path, 'echo launch >> "$FLOWFIELD_RUNTIME_DIR/starts"; sleep 60'
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            replies = await asyncio.gather(*(runtime.run("harbor", value.id) for _ in range(4)))
            assert {reply.id for reply in replies} == {value.id}
            await settled(runtime, value, {"running"})
            await asyncio.sleep(0.1)
            assert (Path(value.workspace).parent / "runtime/starts").read_text().splitlines() == [
                "launch"
            ]
            with service.workspace.connection() as db:
                task = service.workspace._task(db, "harbor", "work")
                assert "Stop the result runtime" in TaskDeletionService(service.workspace).blocker(
                    db, task
                )
            await runtime.stop_task("harbor", "work")
            assert runtime.copies.get("harbor", value.id).runtime_state == "stopped"
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_uncertain_cleanup_blocks_replacement_until_confirmed(tmp_path, monkeypatch):
    async def exercise():
        service, version = await asyncio.to_thread(configured, tmp_path)
        runtime = service.result_runtimes
        value = await prepared(service, version)
        # A missing receipt cannot be treated as proof of successful termination.
        value.runtime_state = "uncertain"
        receipt = (
            Path(value.workspace).parent / "runtime" / f"owner-{value.runtime_generation}-lost.json"
        )
        receipt.write_text('{"cleanup_confirmed": false}')
        runtime.copies._save(value)
        with pytest.raises(ApplicationError, match="cleanup|Cleanup"):
            await runtime.stop_task("harbor", "work")
        with pytest.raises(ApplicationError):
            await runtime.prepare(
                "harbor",
                InspectionPrepare(
                    result_id=version.id, expected_revision=version.revision, new_copy=True
                ),
            )
        await runtime.confirm_stopped("harbor", value.id)
        next_value = await prepared(service, version)
        assert next_value.runtime_state == "ready"
        assert receipt.read_text() == '{"cleanup_confirmed": false}'
        await runtime.close()

    asyncio.run(exercise())


def test_runtime_setup_failure_and_readiness_are_separate_from_code_validation(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured,
            tmp_path,
            'touch "$FLOWFIELD_RUNTIME_DIR/available"; sleep 60',
            ready_command='test -f "$FLOWFIELD_RUNTIME_DIR/available"',
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            running = await settled(runtime, value, {"running", "failed", "uncertain"})
            assert running.runtime_state == "running", running.runtime_problem
            await runtime.stop("harbor", value.id)
            assert not current(service).approved_at
            # Saved preparation failure does not rewrite immutable validation evidence.
            copy = runtime.copies.get("harbor", value.id)
            copy.setup_commands = ["exit 12"]
            runtime.copies._save(copy)
            copy.runtime_state = "preparing"
            await runtime._prepare(copy)
            assert copy.runtime_state == "failed"
            assert "12" in copy.runtime_problem
            assert current(service).candidate_commit == version.candidate_commit
        finally:
            await runtime.close()

    asyncio.run(exercise())


def python_command(source):
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def test_batch_result_finishes_repeats_and_preserves_generated_files(tmp_path):
    async def exercise():
        command = python_command(
            "import os,pathlib; p=pathlib.Path(os.environ['FLOWFIELD_OUTPUT_DIR'])/'report.txt'; "
            "p.write_text('résumé: 42\\n'); print(p.read_text(), end='')"
        )
        service, version = await asyncio.to_thread(configured, tmp_path, command)
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            for _ in range(2):
                await runtime.run("harbor", value.id)
                done = await settled(runtime, value, {"ready", "finished", "failed"})
                assert done.runtime_state == "finished", done.runtime_problem
                assert "résumé: 42" in done.runtime_output
                assert done.runtime_prepared and not done.locally_changed
                assert (Path(done.workspace).parent / "runtime/output/report.txt").read_text() == (
                    "résumé: 42\n"
                )
            assert current(service).candidate_commit == version.candidate_commit
            assert not current(service).approved_at
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_noninteractive_stdin_is_eof_not_the_guardian_lease(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured,
            tmp_path,
            python_command("import sys; print('stdin:', repr(sys.stdin.read()))"),
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            async with asyncio.timeout(3):
                done = await settled(runtime, value, {"ready", "finished", "failed"})
            assert "stdin: ''" in done.runtime_output
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_failed_launch_does_not_wait_for_hung_readiness_probe(tmp_path):
    async def exercise():
        probe_pid = tmp_path / "probe.pid"
        service, version = await asyncio.to_thread(
            configured,
            tmp_path,
            "sleep 0.2; exit 9",
            ready_command=f"echo $$ > {shlex.quote(str(probe_pid))}; exec sleep 60",
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            async with asyncio.timeout(3):
                failed = await settled(runtime, value, {"failed", "uncertain"})
            assert failed.runtime_state == "failed"
            assert "9" in failed.runtime_problem
            with pytest.raises(ProcessLookupError):
                os.kill(int(probe_pid.read_text()), 0)
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_split_utf8_output_preserves_characters(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured,
            tmp_path,
            python_command(
                "import os,time; os.write(1,b'caf\\xc3'); time.sleep(.2); "
                "os.write(1,b'\\xa9 \\xf0\\x9f'); time.sleep(.2); os.write(1,b'\\x8e\\xae\\n')"
            ),
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            done = await settled(runtime, value, {"ready", "finished", "failed"})
            assert done.runtime_output == "café 🎮\n"
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_immediate_launch_failure_cleans_a_cancelled_readiness_start(tmp_path):
    async def exercise():
        service, version = await asyncio.to_thread(
            configured,
            tmp_path,
            "exec /definitely-missing-flowfield-fixture-executable",
            ready_command="test -f /definitely-missing-flowfield-fixture-ready",
        )
        runtime = service.result_runtimes
        try:
            for _ in range(6):
                value = await prepared(service, version)
                await runtime.run("harbor", value.id)
                failed = await settled(runtime, value, {"failed", "uncertain"})
                assert failed.runtime_state == "failed", failed.runtime_problem
                assert any(str(code) in failed.runtime_problem for code in (126, 127))
                assert all(runtime._clean(path) for path in runtime._receipts(failed))
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_cancel_before_readiness_guardian_launch_still_records_cleanup(tmp_path, monkeypatch):
    from flowfield.adapters.local_process import LocalProcess

    start = LocalProcess.start

    async def delayed(command, **kwargs):
        if command[-1] == "sleep 10":
            await asyncio.sleep(0.2)
        return await start(command, **kwargs)

    monkeypatch.setattr(LocalProcess, "start", delayed)

    async def exercise():
        service, version = await asyncio.to_thread(
            configured, tmp_path, "exit 17", ready_command="sleep 10"
        )
        runtime = service.result_runtimes
        try:
            value = await prepared(service, version)
            await runtime.run("harbor", value.id)
            failed = await settled(runtime, value, {"failed", "uncertain"})
            assert failed.runtime_state == "failed", failed.runtime_problem
            assert "17" in failed.runtime_problem
            assert all(runtime._clean(path) for path in runtime._receipts(failed))
        finally:
            await runtime.close()

    asyncio.run(exercise())
