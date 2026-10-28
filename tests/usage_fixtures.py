"""Usage tests allocate real immutable owners, never invent result-based accounting."""

from execution_fixtures import BASE, fixture

from flowfield.usage import Usage
from flowfield.usage_models import Consumption, Tokens, UsageFilter
from flowfield.usage_reads import UsageReads


def accounting(tmp_path, *, count=1):
    execution = fixture(tmp_path, count=count, cap=count)
    runs = [execution.claim("harbor", BASE, {BASE: set()}) for _ in range(count)]
    usage = Usage(execution.workspace)
    for run in runs:
        usage.start("harbor", "run", run.id, run.id)
    return execution, usage, runs


def event(run, n=100, *, identity="result-1", **updates):
    return Consumption.model_validate(
        {
            "event_id": identity,
            "session_id": run.id,
            "native_version": "fixture-version",
            "model": "reported-model",
            "counters": Tokens(
                input=n, output=n // 5, cache_read=n // 2, cache_write=n // 10, reasoning=n // 10
            ),
            "final": True,
            **updates,
        }
    )


def summary(execution, **filters):
    return UsageReads(execution.workspace).view("harbor", UsageFilter(**filters))
