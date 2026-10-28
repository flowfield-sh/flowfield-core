"""Pi RPC 1.1.0/1.4.0 full-session statistics, never added to message usage.

getSessionStats sums all session entries, including tools and compaction. It has
no model partition or provider-omission bit: retain that coverage gap explicitly.
"""

import hashlib
from typing import Any, Literal

from flowfield.usage_models import Consumption, Tokens


def counters(value: Any) -> Tokens:
    value = value if isinstance(value, dict) else {}
    return Tokens.model_validate(
        {
            field: number
            if type(number := value.get(native)) is int and 0 <= number <= 9_007_199_254_740_991
            else None
            for native, field in {
                "input": "input",
                "output": "output",
                "cacheRead": "cache_read",
                "cacheWrite": "cache_write",
                "total": "total",
            }.items()
        }
    )


def observation(
    stats: dict[str, Any],
    *,
    session: str,
    dispatch: str,
    version: str | None,
    inherited: Tokens | None,
    final: bool,
    failed: bool,
) -> Consumption | None:
    if stats.get("sessionId") != session:
        return None
    value = counters(stats.get("tokens"))
    supported = version in {"1.1.0", "1.4.0"}
    digest = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
    optional: tuple[Literal["input", "output", "cache_read", "cache_write", "reasoning"], ...] = (
        "input",
        "output",
        "cache_read",
        "cache_write",
    )
    return Consumption(
        event_id=f"{dispatch}:{digest}:{int(final)}",
        dispatch_id=dispatch,
        session_id=session,
        contract="pi-session-stats/1",
        adapter_version="pi-rpc-v1",
        native_version=version,
        scope="native-session-all-entries",
        counters=value,
        uncertain_components=[key for key in optional if getattr(value, key) == 0],
        semantics="disjoint-cache",
        baseline="resume",
        inherited=inherited,
        final=final and not failed and supported,
        gap="unsupported-version"
        if not supported
        else "native-error"
        if failed
        else "missing-counters"
        if value.total in (None, 0)
        else None,
        limitations=["native-provider-coverage-unknown"],
    )
