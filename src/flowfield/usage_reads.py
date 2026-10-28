"""Bounded usage views: receipt-time tokens and separately clipped execution intervals."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from flowfield.application import Workspace
from flowfield.usage import Usage, add_tokens
from flowfield.usage_models import (
    Consumption,
    Source,
    Tokens,
    UsageBreakdown,
    UsageBucket,
    UsageEvidence,
    UsageEvidencePage,
    UsageExecution,
    UsageFilter,
    UsageTotals,
    UsageView,
)

MAX_EXECUTIONS = 10_000
MAX_READ_OBSERVATIONS = 100_000


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def elapsed(intervals: list[tuple[datetime, datetime]]) -> float | None:
    """Wall-clock running time: merge overlap before counting parallel activity."""
    if not intervals:
        return None
    total = 0.0
    left, right = sorted(intervals)[0]
    for start, end in sorted(intervals)[1:]:
        if start > right:
            total += (right - left).total_seconds()
            left, right = start, end
        else:
            right = max(right, end)
    return total + (right - left).total_seconds()


def buckets(filters: UsageFilter) -> list[UsageBucket]:
    if filters.start is None or filters.end is None:
        return []
    zone = ZoneInfo(filters.timezone)
    local = filters.start.astimezone(zone)
    cursor = (
        local.replace(minute=0, second=0, microsecond=0)
        if filters.group == "hour"
        else local.replace(hour=0, minute=0, second=0, microsecond=0)
    ).astimezone(UTC)
    result = []
    while cursor < filters.end:
        right = (
            cursor + timedelta(hours=1)
            if filters.group == "hour"
            else (cursor.astimezone(zone) + timedelta(days=1))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(UTC)
        )
        result.append(
            UsageBucket(
                start=max(cursor, filters.start).astimezone(UTC).isoformat(),
                end=min(right, filters.end).astimezone(UTC).isoformat(),
                label=cursor.astimezone(zone).isoformat(),
            )
        )
        cursor = right
    return result


def execution_record(row: Any, observations: list[Any]) -> UsageExecution:
    data = dict(row)
    data["legacy"] = bool(data["legacy"])
    data["reasons"] = json.loads(data["reasons"])
    accepted = [r for r in observations if r["disposition"] == "accepted"]
    latest: dict[str, Consumption] = {}
    models: list[str | None] = []
    for item in observations:
        event = Consumption.model_validate_json(item["data"])
        if event.model not in models:
            models.append(event.model)
        if item["disposition"] == "accepted":
            latest[item["series"]] = event
    data["tokens"] = add_tokens([Tokens.model_validate_json(r["delta"]) for r in accepted])
    if (
        accepted
        and data["tokens"].total is None
        and any(Tokens.model_validate_json(r["delta"]).total is not None for r in accepted)
    ):
        data["reasons"].append("aggregate-token-overflow")
    data["observation_count"] = len(observations)
    data["observed_models"] = models
    # Count distinct terminal results, not model partitions of the same result.
    final_events = set(
        e.dispatch_id or e.event_id
        for r in accepted
        if (e := Consumption.model_validate_json(r["data"])).final
        and e.native_scope_id in (None, e.session_id)
    )
    data["coverage"] = (
        "complete"
        if latest
        and all(e.final for e in latest.values())
        and not data["reasons"]
        and len(final_events) >= data["dispatch_count"]
        else "partial"
        if accepted
        else "unavailable"
    )
    if not observations and "legacy-no-consumption" not in data["reasons"]:
        data["reasons"].append("no-consumption-reported")
    if data["timing"] in ("live", "unknown-end") and data["coverage"] == "complete":
        data["coverage"] = "partial"
    return UsageExecution.model_validate(data)


class UsageReads:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def evidence(
        self, workspace: str, source: Source, identity: str, after: int = 0, limit: int = 50
    ) -> UsageEvidencePage:
        limit = max(1, min(limit, 100))
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace)
            owner = Usage.owner(db, workspace, source, identity)
            rows = db.execute(
                "SELECT * FROM usage_observations WHERE execution=? ORDER BY number LIMIT 4096",
                (owner["number"],),
            ).fetchall()
            execution = execution_record(owner, rows)
            page = [r for r in rows if r["number"] > after][: limit + 1]
            return UsageEvidencePage(
                execution=execution,
                items=[
                    UsageEvidence(
                        number=r["number"],
                        received_at=r["received_at"],
                        observation=Consumption.model_validate_json(r["data"]),
                        baseline=Tokens.model_validate_json(r["baseline"])
                        if r["baseline"]
                        else None,
                        delta=Tokens.model_validate_json(r["delta"]),
                        disposition=r["disposition"],
                        interval_start=owner["started_at"],
                        interval_end=owner["ended_at"],
                        timing="execution-bounded"
                        if owner["started_at"] and owner["ended_at"]
                        else "unknown",
                    )
                    for r in page[:limit]
                ],
                next_after=page[limit - 1]["number"] if len(page) > limit else None,
            )

    def view(self, workspace: str, filters: UsageFilter, *, task: str | None = None) -> UsageView:
        conditions, parameters = ["workspace_id=?"], [workspace]
        with self.workspace.connection() as db:
            self.workspace._workspace(db, workspace)
            if task is not None:
                task = self.workspace._task(db, workspace, task).id
                filters = filters.model_copy(update={"task_id": task})
            for field in (
                "task_id",
                "related_task_id",
                "project_id",
                "role",
                "source",
                "purpose",
                "outcome",
                "harness",
            ):
                value = getattr(filters, field)
                if value is not None:
                    conditions.append(f"{field}=?")
                    parameters.append(value)
            if filters.unassigned:
                conditions.append("task_id IS NULL")
            if filters.start and filters.end:
                period_left, period_right = (
                    filters.start.astimezone(UTC).isoformat(),
                    filters.end.astimezone(UTC).isoformat(),
                )
                conditions.append(
                    "((julianday(created_at)>=julianday(?) AND julianday(created_at)<julianday(?)) "
                    "OR (julianday(started_at)<julianday(?) AND "
                    "(julianday(ended_at)>julianday(?) OR timing='live')) "
                    "OR (julianday(started_at)>=julianday(?) "
                    "AND julianday(started_at)<julianday(?)) "
                    "OR EXISTS(SELECT 1 FROM usage_observations p "
                    "WHERE p.execution=usage_executions.number "
                    "AND julianday(p.received_at)>=julianday(?) "
                    "AND julianday(p.received_at)<julianday(?)))"
                )
                parameters.extend(
                    [
                        period_left,
                        period_right,
                        period_right,
                        period_left,
                        period_left,
                        period_right,
                        period_left,
                        period_right,
                    ]
                )
            where = " AND ".join(conditions)
            rows = db.execute(
                f"SELECT * FROM usage_executions WHERE {where} ORDER BY number LIMIT ?",
                (*parameters, MAX_EXECUTIONS + 1),
            ).fetchall()
            limited = len(rows) > MAX_EXECUTIONS
            rows = rows[:MAX_EXECUTIONS]
            observations: dict[int, list[Any]] = defaultdict(list)
            # Filter joins at the database boundary; source reads remain bounded even
            # for a lifetime task with thousands of native snapshots.
            read_count = 0
            for row in db.execute(
                f"SELECT o.* FROM usage_observations o JOIN usage_executions e "
                f"ON e.number=o.execution WHERE e.number IN (SELECT number FROM "
                f"usage_executions WHERE {where} ORDER BY number LIMIT ?) ORDER "
                f"BY o.number LIMIT ?",
                (*parameters, MAX_EXECUTIONS, MAX_READ_OBSERVATIONS + 1),
            ):
                read_count += 1
                if read_count > MAX_READ_OBSERVATIONS:
                    limited = True
                    break
                observations[row["execution"]].append(row)
            collection = db.execute(
                "SELECT collection_started_at FROM usage_metadata WHERE id=1"
            ).fetchone()[0]
            related = (
                db.execute(
                    "SELECT count(*) FROM usage_executions WHERE workspace_id=? "
                    "AND related_task_id=? AND source='coordinator_turn'",
                    (workspace, task),
                ).fetchone()[0]
                if task
                else 0
            )
        groups = buckets(filters)
        bucket_tokens: list[list[Tokens]] = [[] for _ in groups]
        totals = UsageTotals()
        measured_at = datetime.now(UTC)
        intervals: list[tuple[datetime, datetime]] = []
        role_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        live_roles: set[str] = set()
        contributions: list[Tokens] = []
        items: list[UsageExecution] = []
        breakdown: dict[tuple[str, str | None], tuple[list[Tokens], set[int]]] = {}
        for row in rows:
            own = observations[row["number"]]
            execution = execution_record(row, own)
            if filters.coverage and execution.coverage != filters.coverage:
                continue
            selected = []
            models: dict[str | None, list[Tokens]] = defaultdict(list)
            for receipt in own:
                event = Consumption.model_validate_json(receipt["data"])
                if filters.model is not None and event.model != filters.model:
                    continue
                if filters.unknown_model and event.model is not None:
                    continue
                if (
                    filters.start
                    and filters.end
                    and not filters.start <= instant(receipt["received_at"]) < filters.end
                ):
                    continue
                selected.append(receipt)
                if receipt["disposition"] == "accepted":
                    delta = Tokens.model_validate_json(receipt["delta"])
                    contributions.append(delta)
                    models[event.model].append(delta)
                    for index, bucket in enumerate(groups):
                        if (
                            instant(bucket.start)
                            <= instant(receipt["received_at"])
                            < instant(bucket.end)
                        ):
                            bucket_tokens[index].append(delta)
                            break
            if filters.model is not None and filters.model not in execution.observed_models:
                continue
            if (
                filters.unknown_model
                and execution.observed_models
                and None not in execution.observed_models
            ):
                continue
            start = instant(execution.started_at) if execution.started_at else None
            end = instant(execution.ended_at) if execution.ended_at else None
            known_end = measured_at if execution.timing == "live" else end
            if start and known_end and known_end >= start:
                left = max(start, filters.start) if filters.start else start
                right = min(known_end, filters.end) if filters.end else known_end
                if right >= left:
                    intervals.append((left, right))
                    role_intervals[execution.role].append((left, right))
                    if execution.timing == "live" and right == measured_at:
                        live_roles.add(execution.role)
            if filters.start and filters.end:
                overlaps = (
                    start is not None
                    and end is not None
                    and start < filters.end
                    and end > filters.start
                )
                created = filters.start <= instant(execution.created_at) < filters.end
                started = start is not None and filters.start <= start < filters.end
                live = execution.timing == "live" and start is not None and start < filters.end
                if not selected and not overlaps and not created and not started and not live:
                    continue
            totals.attempts += 1
            if start and (
                filters.start is None or filters.end is None or filters.start <= start < filters.end
            ):
                totals.started_attempts += 1
            elif start is None and execution.legacy:
                totals.unknown_starts += 1
            setattr(totals, execution.coverage, getattr(totals, execution.coverage) + 1)
            totals.legacy += execution.legacy
            if start and end and end >= start:
                left = max(start, filters.start) if filters.start else start
                right = min(end, filters.end) if filters.end else end
                totals.recorded_intervals += 1
                totals.recorded_seconds = (totals.recorded_seconds or 0) + max(
                    0, (right - left).total_seconds()
                )
                for bucket in groups:
                    bucket.recorded_seconds = (bucket.recorded_seconds or 0) + max(
                        0,
                        (
                            min(end, instant(bucket.end)) - max(start, instant(bucket.start))
                        ).total_seconds(),
                    )
            elif execution.timing == "live":
                totals.live_intervals += 1
            elif execution.timing in ("unknown-end", "legacy"):
                totals.unknown_intervals += 1
            period_tokens = add_tokens(
                [
                    Tokens.model_validate_json(r["delta"])
                    for r in selected
                    if r["disposition"] == "accepted"
                ]
            )
            execution = execution.model_copy(update={"tokens": period_tokens})
            items.append(execution)
            if not models:
                models[None] = []
            for dimension, key in (
                ("role", execution.role),
                ("source", execution.source),
                ("task", execution.task_id),
                ("harness", execution.harness),
                ("purpose", execution.purpose),
                ("outcome", execution.outcome),
            ):
                entry = breakdown.setdefault((dimension, key), ([], set()))
                entry[0].append(period_tokens)
                entry[1].add(execution.number)
            for model, values in models.items():
                entry = breakdown.setdefault(("model", model), ([], set()))
                entry[0].extend(values)
                entry[1].add(execution.number)
        totals.tokens = add_tokens(contributions)
        totals.elapsed_seconds = elapsed(intervals)
        if totals.tokens.total is None and any(value.total is not None for value in contributions):
            totals.reasons.append("aggregate-token-overflow")
        totals.coverage = (
            "complete"
            if totals.attempts
            and totals.tokens.total is not None
            and totals.complete == totals.attempts
            and not limited
            and not totals.reasons
            else "partial"
            if totals.tokens.total is not None or totals.reasons
            else "unavailable"
        )
        for bucket, values in zip(groups, bucket_tokens, strict=True):
            bucket.tokens = add_tokens(values)
        page = [item for item in items if item.number > filters.after][: filters.limit + 1]
        return UsageView(
            measured_at=measured_at.isoformat(),
            collection_started_at=collection,
            totals=totals,
            items=page[: filters.limit],
            next_after=page[filters.limit - 1].number if len(page) > filters.limit else None,
            related_coordinator_attempts=related,
            buckets=groups,
            breakdowns=[
                UsageBreakdown.model_validate(
                    {
                        "dimension": dim,
                        "key": key,
                        "tokens": add_tokens(values),
                        "attempts": len(ids),
                        "elapsed_seconds": elapsed(role_intervals[key])
                        if dim == "role" and key is not None
                        else None,
                        "live": dim == "role" and key in live_roles,
                    }
                )
                for (dim, key), (values, ids) in breakdown.items()
            ],
            filters=filters,
            limit_reached=limited,
        )
