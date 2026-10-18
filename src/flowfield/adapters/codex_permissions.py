"""Present native Codex command decisions without widening their offered scope."""

import json
import shlex
from typing import Any


def command_decisions(
    data: dict[str, Any],
) -> tuple[tuple[tuple[str, str, str], ...], dict[str, Any]]:
    offered = data.get("availableDecisions")
    if offered is not None and (not isinstance(offered, list) or len(offered) > 16):
        return (), {}
    if offered is None:
        offered = ["accept", "decline"]
        prefix = data.get("proposedExecpolicyAmendment")
        if prefix:
            offered.append({"acceptWithExecpolicyAmendment": {"execpolicy_amendment": prefix}})
    options: list[tuple[str, str, str]] = []
    decisions: dict[str, Any] = {}
    simple: dict[str, tuple[str, str, str]] = {
        "accept": ("allow", "Allow once", "allow_once"),
        "acceptForSession": ("session", "Allow for this session", "allow_always"),
        "decline": ("deny", "Deny", "reject_once"),
        "cancel": ("cancel", "Cancel request", "reject_once"),
    }
    for index, decision in enumerate(offered):
        if isinstance(decision, str):
            option = simple.get(decision)
            if option and option[0] not in decisions:
                options.append(option)
                decisions[option[0]] = decision
        elif isinstance(decision, dict) and set(decision) == {"acceptWithExecpolicyAmendment"}:
            amendment = decision["acceptWithExecpolicyAmendment"]
            if not isinstance(amendment, dict) or set(amendment) != {"execpolicy_amendment"}:
                continue
            prefix = amendment["execpolicy_amendment"]
            if not isinstance(prefix, list) or not 0 < len(prefix) <= 100:
                continue
            if not all(isinstance(arg, str) and arg and len(arg) <= 2000 for arg in prefix):
                continue
            label = "Always allow in Codex: " + shlex.join(prefix)
            if len(label) > 12000:
                continue
            identity = f"policy:{index}"
            options.append((identity, label, "allow_always"))
            decisions[identity] = json.loads(json.dumps(decision))
    return tuple(options), decisions
