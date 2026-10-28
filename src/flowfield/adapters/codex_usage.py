"""Codex 0.164.0 thread totals; no context, account totals or transcript inspection.

Protocol TokenUsageInfo appends response usage; resume replays the saved snapshot.
Each thread owns its counters. Forked children require their own inherited baseline.
The ledger quarantines decreases and the inconsistent context-full sentinel.
"""

import hashlib
from typing import Any, Literal

from flowfield.usage_models import Consumption, Tokens


def counters(value: Any) -> Tokens:
    fields = {
        "inputTokens": "input",
        "outputTokens": "output",
        "cachedInputTokens": "cache_read",
        "cacheWriteInputTokens": "cache_write",
        "reasoningOutputTokens": "reasoning",
        "totalTokens": "total",
    }
    value = value if isinstance(value, dict) else {}
    return Tokens.model_validate(
        {
            field: number
            if type(number := value.get(native)) is int and 0 <= number <= 9_007_199_254_740_991
            else None
            for native, field in fields.items()
        }
    )


def observation(
    value: Tokens,
    *,
    session: str,
    thread: str,
    turn: str,
    version: str | None,
    resumed: bool,
    inherited: Tokens | None,
    final: bool = False,
    failed: bool = False,
) -> Consumption:
    # No native event ID/order is supplied. The canonical vector identifies replays;
    # final reconciliation is a distinct zero-delta observation for this native turn.
    digest = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
    optional: tuple[Literal["input", "output", "cache_read", "cache_write", "reasoning"], ...] = (
        "cache_read",
        "cache_write",
        "reasoning",
    )
    return Consumption(
        event_id=f"{turn}:{digest}:{int(final)}"[:200],
        dispatch_id=turn,
        session_id=session,
        native_scope_id=thread,
        contract="codex-thread-total/0.164.0",
        adapter_version="codex-app-server-v1",
        native_version=version,
        scope="native-thread",
        counters=value,
        uncertain_components=[key for key in optional if getattr(value, key) == 0],
        semantics="inclusive-cache",
        baseline="resume" if resumed else "fresh",
        inherited=inherited,
        final=final and not failed and version == "0.164.0",
        gap="unsupported-version"
        if version != "0.164.0"
        else "native-error"
        if failed
        else "missing-counters"
        if value.input is None or value.output is None or value.total is None
        else None,
    )
