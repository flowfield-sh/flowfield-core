"""Explicit model choices for isolated tests; never launches a native model."""

from flowfield.agent_models import AgentChoice

TEST_AGENTS = {
    role: AgentChoice(harness="codex", model="test-model", fast=False)
    for role in ("coordinator", "worker", "reviewer")
}
