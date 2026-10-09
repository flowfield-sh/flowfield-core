"""Conservative continuity policy for measured native implementations."""

from flowfield.agent_models import AgentChoice


def retains_session(before: AgentChoice | None, after: AgentChoice | None) -> bool:
    if before is None or after is None or before.harness != after.harness:
        return False
    if after.harness == "codex":
        return True  # Native reconfiguration of an existing session is measured.
    # Claude query replacement/control changes are not assumed portable. Exact
    # settings can resume; changed controls use a fresh native session initially.
    return before == after
