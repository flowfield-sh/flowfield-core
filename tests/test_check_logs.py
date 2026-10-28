"""Command transcripts retain terminal summaries and bounded, verified bytes."""

import asyncio
import os
from pathlib import Path

import pytest

from flowfield.adapters import local_checks
from flowfield.adapters.local_execution import LocalHost
from flowfield.check_logs import download
from flowfield.errors import ApplicationError


@pytest.mark.parametrize("cap", [256, 8 * 1024 * 1024])
def test_command_log_keeps_final_summary_and_marks_byte_limit(tmp_path, monkeypatch, cap):
    environment = LocalHost(os.environ).prepare_files(tmp_path, "workspace", "attempt")
    monkeypatch.setattr(local_checks, "MAX_LOG_BYTES", cap)
    script = (
        "printf 'BEGIN\\n'; head -c 24000 /dev/zero | tr '\\000' x; printf '\\nFINAL 26 passed\\n'"
    )
    checks = asyncio.run(local_checks.run_checks(environment, [script], 5))
    check = checks[0]
    assert check.exit_code == 0 and check.truncated
    assert check.output.startswith("BEGIN\n") and check.output.endswith("FINAL 26 passed\n")
    assert check.started_at and check.elapsed_seconds > 0
    assert check.log_truncated == (cap == 256)
    response = download(environment.runtime, checks, 0)
    path = Path(response.path)
    data = path.read_bytes()
    assert len(data) == check.log_size <= cap
    if cap > 256:
        assert data.endswith(b"FINAL 26 passed\n")
    path.write_bytes(b"changed")
    with pytest.raises(ApplicationError, match="missing or changed"):
        download(environment.runtime, checks, 0)
    with pytest.raises(ApplicationError, match="not found"):
        download(environment.runtime, checks, -1)
