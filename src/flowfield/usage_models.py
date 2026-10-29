"""Usage v1 wire and adapter contracts. Token components overlap, never sum all fields."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

Count = Annotated[StrictInt, Field(ge=0, le=9_007_199_254_740_991)]
Key = Annotated[str, Field(min_length=1, max_length=200)]
Source = Literal["run", "review_attempt", "coordinator_turn"]
Role = Literal["worker", "reviewer", "coordinator"]
Coverage = Literal["complete", "partial", "unavailable"]


class UsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Tokens(UsageRecord):
    input: Count | None = None
    output: Count | None = None
    cache_read: Count | None = None
    cache_write: Count | None = None
    reasoning: Count | None = None
    total: Count | None = None


class Consumption(UsageRecord):
    """Allowlisted native cumulative evidence; ownership is bound by the service callback."""

    event_id: Key
    session_id: Key
    epoch: Key = "0"
    native_scope_id: Key | None = None
    dispatch_id: Key | None = None
    order: Count | None = None
    source_time: AwareDatetime | None = None
    contract: Literal[
        "claude-model-usage/0.3.293", "codex-thread-total/0.164.0", "pi-session-stats/1"
    ] = "claude-model-usage/0.3.293"
    native_version: Key | None = None
    adapter_version: Literal["claude-sdk-v1", "codex-app-server-v1", "pi-rpc-v1"] = "claude-sdk-v1"
    scope: Literal[
        "query-pipeline-including-native-children", "native-thread", "native-session-all-entries"
    ] = "query-pipeline-including-native-children"
    model: Key | None = None
    counters: Tokens
    uncertain_components: list[
        Literal["input", "output", "cache_read", "cache_write", "reasoning", "total"]
    ] = Field(default_factory=list, max_length=6)
    semantics: Literal["disjoint-cache", "inclusive-cache"] = "disjoint-cache"
    baseline: Literal["fresh", "resume", "fork", "explicit-reset"] = "fresh"
    inherited: Tokens | None = None
    final: bool = False
    gap: Literal["native-error", "missing-counters", "unsupported-version"] | None = None
    limitations: list[
        Literal["native-provider-coverage-unknown", "native-child-baseline-unknown"]
    ] = Field(default_factory=list, max_length=2)


class UsageExecution(UsageRecord):
    number: int
    workspace_id: str
    source: Source
    source_id: str
    task_id: str | None
    related_task_id: str | None
    project_id: str | None
    parent_id: str | None
    subtask_id: str | None
    role: Role
    purpose: str
    outcome: str
    harness: str | None
    requested_model: str | None
    session_id: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None
    timing: Literal["not-dispatched", "recorded", "live", "unknown-end", "legacy"]
    legacy: bool
    coverage: Coverage
    reasons: list[str] = Field(default_factory=list)
    tokens: Tokens = Field(default_factory=Tokens)
    observed_models: list[str | None] = Field(default_factory=list)
    observation_count: int = 0
    dispatch_count: int = 0


class UsageEvidence(UsageRecord):
    number: int
    received_at: str
    observation: Consumption
    baseline: Tokens | None
    delta: Tokens
    disposition: str
    interval_start: str | None = None
    interval_end: str | None = None
    timing: Literal["execution-bounded", "unknown"] = "unknown"


class UsageEvidencePage(UsageRecord):
    execution: UsageExecution
    items: list[UsageEvidence]
    next_after: int | None = None


class UsageFilter(UsageRecord):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    timezone: str = Field(default="UTC", max_length=100)
    group: Literal["minute", "hour", "day"] = "day"
    dimension: Literal["role", "harness", "model"] = "role"
    task_id: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    source: Source | None = None
    purpose: str | None = Field(default=None, max_length=100)
    outcome: str | None = Field(default=None, max_length=100)
    harness: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    coverage: Coverage | None = None
    related_task_id: str | None = Field(default=None, max_length=200)
    unassigned: bool = False
    unknown_model: bool = False
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def period(self) -> Self:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose an IANA timezone") from None
        if (self.start is None) != (self.end is None):
            raise ValueError("Supply both start and end")
        if self.start and self.end:
            seconds = (self.end - self.start).total_seconds()
            if seconds <= 0 or seconds > 366 * 86400:
                raise ValueError("Choose a positive period of at most 366 days")
            if self.group == "hour" and seconds > 31 * 86400:
                raise ValueError("Hourly views cover at most 31 days")
            if self.group == "minute" and seconds > 86400:
                raise ValueError("Minute views cover at most one day")
        return self


class UsageTotals(UsageRecord):
    reasons: list[str] = Field(default_factory=list)
    tokens: Tokens = Field(default_factory=Tokens)
    attempts: int = 0
    started_attempts: int = 0
    corrections: int | None = 0
    unknown_starts: int = 0
    complete: int = 0
    partial: int = 0
    unavailable: int = 0
    legacy: int = 0
    recorded_seconds: float | None = None
    elapsed_seconds: float | None = None
    recorded_intervals: int = 0
    unknown_intervals: int = 0
    live_intervals: int = 0
    coverage: Coverage = "unavailable"


class UsageBucket(UsageRecord):
    start: str
    end: str
    label: str
    tokens: Tokens = Field(default_factory=Tokens)
    recorded_seconds: float | None = None
    elapsed_seconds: float | None = None


class UsageSeriesPoint(UsageRecord):
    tokens: Tokens = Field(default_factory=Tokens)
    elapsed_seconds: float | None = None


class UsageSeries(UsageRecord):
    key: str | None
    other: bool = False
    points: list[UsageSeriesPoint]


class UsageBreakdown(UsageRecord):
    dimension: Literal["role", "source", "task", "harness", "model", "purpose", "outcome"]
    key: str | None
    tokens: Tokens
    attempts: int
    elapsed_seconds: float | None = None
    live: bool = False


class UsageContract(UsageRecord):
    harness: str
    contract: str | None
    native_version: str | None
    supported: bool
    scope: str
    limitation: str


def contracts() -> list[UsageContract]:
    return [
        UsageContract(
            harness="claude-code",
            contract="claude-model-usage/0.3.293",
            native_version="2.1.293",
            supported=True,
            scope="Query pipeline including native children; cumulative per reported model",
            limitation="Reported estimates for this scope; excludes helpers outside the pipeline. "
            "Resumed history requires a known baseline. Other native versions are unavailable.",
        ),
        UsageContract(
            harness="codex",
            contract="codex-thread-total/0.164.0",
            native_version="0.164.0",
            supported=True,
            scope="Cumulative native thread; bound child threads have separate series",
            limitation="Reported counters. Resumes/forks require a baseline; "
            "decreases and context-full sentinels are quarantined. Model is unreported.",
        ),
        UsageContract(
            harness="pi",
            contract="pi-session-stats/1",
            native_version="1.1.0, 1.4.0",
            supported=True,
            scope="Full-session aggregate including compacted history, tools and summaries",
            limitation="Session statistics cannot establish provider counter completeness or "
            "per-model attribution. Missing provider counters may be native defaults.",
        ),
    ]


class UsageView(UsageRecord):
    measured_at: str
    contracts: list[UsageContract] = Field(default_factory=contracts)
    collection_started_at: str
    totals: UsageTotals
    related_coordinator_attempts: int = 0
    items: list[UsageExecution]
    next_after: int | None = None
    buckets: list[UsageBucket] = Field(default_factory=list)
    series: list[UsageSeries] = Field(default_factory=list)
    breakdowns: list[UsageBreakdown] = Field(default_factory=list)
    filters: UsageFilter
    timing_label: str = "Usage reported during the period; consumption may span earlier periods."
    limit_reached: bool = False
