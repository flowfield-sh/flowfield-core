"""Local integration evidence, separate from acceptance and worker attempts."""

from typing import Literal

from pydantic import Field

from flowfield.execution_models import CheckResult, Record

DELIVERY_BLOCKERS = frozenset(
    {
        "checkout_dirty",
        "checkout_branch_changed",
        "checkout_busy",
        "checkout_unsupported",
        "checkout_blocked",
        "delivery_uncertain",
    }
)


class CheckoutBinding(Record):
    path: str
    git_dir: str
    device: int
    inode: int
    git_device: int
    git_inode: int


class IntegrationSettings(Record):
    project_id: str
    revision: int = 1
    runtime: Literal["local"] = "local"
    target_branch: str | None = None
    checks: list[str] = Field(default_factory=list)
    setup_commands: list[str] = Field(default_factory=list)
    setup_timeout_seconds: int = 120
    check_timeout_seconds: int = 60


class IntegrationConfig(Record):
    expected_revision: int = Field(ge=1)
    runtime: Literal["local"] | None = None
    target_branch: str = Field(min_length=1, max_length=200)
    create_from: str | None = Field(default=None, min_length=1, max_length=200)
    checks: list[str] = Field(min_length=1, max_length=10)
    setup_commands: list[str] = Field(default_factory=list, max_length=10)
    setup_timeout_seconds: int = Field(default=120, ge=1, le=900)
    check_timeout_seconds: int = Field(default=60, ge=1, le=900)


class IntegrationPrepare(Record):
    expected_revision: int = Field(ge=1)
    author: str = Field(default="agent", min_length=1, max_length=200)


class IntegrationApply(Record):
    expected_revision: int = Field(ge=1)
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    author: str = Field(default="agent", min_length=1, max_length=200)


class IntegrationSummary(Record):
    workspace_id: str
    task_id: str
    id: str
    project_id: str
    run_id: str
    task_key: str
    revision: int = 1
    settings_revision: int
    target_branch: str
    target_before: str
    checkout: CheckoutBinding
    candidate_commit: str | None = None
    target_after: str | None = None
    result_commit: str
    status: Literal["preparing", "ready", "applying", "integrated", "failed", "stale"] = "preparing"
    problem: str | None = None
    created_at: str
    completed_at: str | None = None
    validated_at: str | None = None
    apply_started_at: str | None = None
    checkout_verified_at: str | None = None
    author: str
    workspace: str | None = None
    result_id: str | None = None
    purpose: Literal["preparation", "availability"] = "preparation"
    problem_code: str | None = None


class Integration(IntegrationSummary):
    checks: list[CheckResult] = Field(default_factory=list)
    setup_checks: list[CheckResult] = Field(default_factory=list)


class IntegrationPage(Record):
    items: list[IntegrationSummary]
    next_before: int | None = None
