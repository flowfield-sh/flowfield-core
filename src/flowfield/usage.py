"""Durable numeric accounting. No lifecycle mutation, activity text or native files."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from flowfield.errors import ApplicationError
from flowfield.usage_models import Consumption, Source, Tokens

if TYPE_CHECKING:
    from flowfield.adapters.agent_contract import Agent
    from flowfield.application import Workspace

# Bound each immutable execution's evidence. On exhaustion freeze accounting with an
# explicit gap, rather than discard positive contributions or invent exact history.
MAX_OBSERVATIONS = 4096
MAX_SAFE_TOKENS = 9_007_199_254_740_991
COMPONENTS = tuple(Tokens.model_fields)


def normalize(event: Consumption) -> tuple[Tokens, str | None]:
    raw = event.counters
    values = raw.model_dump()
    if event.semantics == "disjoint-cache":
        inputs = (raw.input, raw.cache_read, raw.cache_write)
        values["input"] = (
            sum(x for x in inputs if x is not None) if all(x is not None for x in inputs) else None
        )
    input_, output = values["input"], raw.output
    total = input_ + output if input_ is not None and output is not None else None
    if raw.total is not None and total is not None and raw.total != total:
        return Tokens(), "inconsistent-total"
    if (
        (raw.reasoning is not None and output is not None and raw.reasoning > output)
        or (raw.cache_read is not None and input_ is not None and raw.cache_read > input_)
        or (raw.cache_write is not None and input_ is not None and raw.cache_write > input_)
    ):
        return Tokens(), "inconsistent-components"
    values["total"] = total
    if any(value is not None and value > MAX_SAFE_TOKENS for value in values.values()):
        return Tokens(), "counter-overflow"
    for key in event.uncertain_components:
        # Native aggregates can contain schema/provider defaults. Preserve the raw
        # numeric evidence, but do not present those components as measured zero.
        values[key] = None
    return Tokens.model_validate(values), None


def add_tokens(values: list[Tokens]) -> Tokens:
    result: dict[str, int | None] = {}
    for key in COMPONENTS:
        parts = [getattr(t, key) for t in values if getattr(t, key) is not None]
        total = sum(parts) if parts else None
        result[key] = total if total is None or total <= MAX_SAFE_TOKENS else None
    return Tokens.model_validate(result)


def _reason(db: sqlite3.Connection, execution: int, reason: str) -> None:
    row = db.execute("SELECT reasons FROM usage_executions WHERE number=?", (execution,)).fetchone()
    if row is None:
        return
    reasons = json.loads(row[0])
    if reason not in reasons:
        reasons.append(reason)
        db.execute(
            "UPDATE usage_executions SET reasons=? WHERE number=?", (json.dumps(reasons), execution)
        )


class Usage:
    def __init__(self, workspace: "Workspace"):
        self.workspace = workspace

    @staticmethod
    def owner(db: sqlite3.Connection, workspace: str, source: Source, identity: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM usage_executions WHERE workspace_id=? AND source=? AND source_id=?",
            (workspace, source, identity),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                "usage_owner_missing", "Execution not found in this workspace.", 404
            )
        return cast(sqlite3.Row, row)

    def bind(self, workspace: str, source: Source, identity: str, agent: "Agent") -> None:
        """Before native dispatch; callback carries authority, never native-supplied task IDs."""
        with self.workspace.connection(write=True, notify=False) as db:
            row = self.owner(db, workspace, source, identity)
            if row["legacy"]:
                _reason(db, row["number"], "collection-started-mid-execution")
            harness = agent.launch.harness if agent.launch else None
            db.execute(
                "UPDATE usage_executions SET harness=coalesce(?,harness) WHERE number=?",
                (harness, row["number"]),
            )
            if harness not in {"claude-code", "codex", "pi"}:
                _reason(db, row["number"], "unsupported-consumption-contract")
        agent.on_consumption = self.callback(workspace, source, identity)

    def callback(
        self, workspace: str, source: Source, identity: str
    ) -> Callable[[Consumption], None]:
        return lambda event: self.observe(workspace, source, identity, event)

    def start(
        self,
        workspace: str,
        source: Source,
        identity: str,
        session: str | None,
        *,
        purpose: str | None = None,
    ) -> None:
        from flowfield.application import now

        with self.workspace.connection(write=True, notify=False) as db:
            row = self.owner(db, workspace, source, identity)
            collision = db.execute(
                "SELECT 1 FROM usage_executions WHERE session_id=? AND harness IS ? "
                "AND number!=? AND "
                "(workspace_id!=? OR source!='coordinator_turn' OR ?!='coordinator_turn') LIMIT 1",
                (session, row["harness"], row["number"], workspace, source),
            ).fetchone()
            if collision:
                # An ambiguous native counter scope disables accounting, not the
                # already-authorized execution (unsupported adapters may use local IDs).
                _reason(db, row["number"], "native-session-owner-conflict")
            db.execute(
                "UPDATE usage_executions SET dispatch_count=dispatch_count+1 WHERE number=?",
                (row["number"],),
            )
            if row["started_at"] is None:
                db.execute(
                    "UPDATE usage_executions SET "
                    "started_at=?,session_id=?,timing='live',purpose=coalesce(?,purpose) "
                    "WHERE number=?",
                    (now(), session, purpose, row["number"]),
                )
            elif row["session_id"] != session:
                raise ApplicationError(
                    "usage_identity_changed", "Execution session identity changed.", 409
                )

    def end(self, workspace: str, source: Source, identity: str, *, confirmed: bool) -> None:
        from flowfield.application import now

        with self.workspace.connection(write=True, notify=False) as db:
            row = self.owner(db, workspace, source, identity)
            if row["started_at"] and row["ended_at"] is None:
                db.execute(
                    "UPDATE usage_executions SET ended_at=?,timing=? WHERE number=?",
                    (
                        now() if confirmed else None,
                        "recorded" if confirmed else "unknown-end",
                        row["number"],
                    ),
                )
                if not confirmed:
                    _reason(db, row["number"], "cleanup-unconfirmed")

    def recover(self) -> None:
        # Only the supervisor holding execution ownership may call this. Readers do not.
        with self.workspace.connection(write=True, notify=False) as db:
            for row in db.execute("SELECT number FROM usage_executions WHERE timing='live'"):
                _reason(db, row[0], "service-restart-unknown-end")
            db.execute("UPDATE usage_executions SET timing='unknown-end' WHERE timing='live'")

    def observe(self, workspace: str, source: Source, identity: str, event: Consumption) -> None:
        from flowfield.application import now

        # Counters contain only numeric/provenance fields; stable receipt excludes replay.
        payload = event.model_dump_json()
        fingerprint = hashlib.sha256(
            event.model_dump_json(exclude={"order", "source_time"}).encode()
        ).hexdigest()
        series = json.dumps(
            [event.contract, event.session_id, event.native_scope_id, event.epoch, event.model],
            separators=(",", ":"),
        )
        with self.workspace.connection(write=True, workspace_id=workspace) as db:
            owner = self.owner(db, workspace, source, identity)
            execution = owner["number"]
            if owner["session_id"] != event.session_id or owner["started_at"] is None:
                raise ApplicationError(
                    "usage_scope_mismatch", "Usage does not match the dispatched execution.", 409
                )
            if db.execute(
                "SELECT 1 FROM usage_observations WHERE execution=? AND "
                "series=? AND event_id=? AND fingerprint=?",
                (execution, series, event.event_id, fingerprint),
            ).fetchone():
                return
            count = db.execute(
                "SELECT count(*) FROM usage_observations WHERE execution=?", (execution,)
            ).fetchone()[0]
            if count >= MAX_OBSERVATIONS:
                _reason(db, execution, "evidence-retention-limit")
                return
            existing = db.execute(
                "SELECT data FROM usage_checkpoints WHERE workspace_id=? AND series=?",
                (workspace, series),
            ).fetchone()
            checkpoint: dict[str, Any] | None = json.loads(existing[0]) if existing else None
            for limitation in event.limitations:
                _reason(db, execution, limitation)
            normalized, error = normalize(event)
            disposition = error or event.gap or "accepted"
            if not event.native_version:
                _reason(db, execution, "native-version-unreported")
            if event.gap == "native-error" and normalized.total:
                # Positive failure totals remain useful; zeroed crash placeholders do not.
                _reason(db, execution, "native-error")
                disposition = error or "accepted"
            baseline: Tokens | None = None
            delta = Tokens()
            # Event identity conflicts and decreasing counters never silently reset.
            if db.execute(
                "SELECT 1 FROM usage_observations WHERE execution=? AND series=? AND event_id=?",
                (execution, series, event.event_id),
            ).fetchone():
                disposition = "conflicting-event"
            if checkpoint:
                baseline = Tokens.model_validate(checkpoint["counters"])
                if checkpoint["execution"] != execution:
                    prior = db.execute(
                        "SELECT * FROM usage_executions WHERE number=?", (checkpoint["execution"],)
                    ).fetchone()
                    from flowfield.usage_reads import execution_record

                    prior_observations = db.execute(
                        "SELECT * FROM usage_observations WHERE execution=? ORDER BY number",
                        (checkpoint["execution"],),
                    ).fetchall()
                    if event.inherited is not None:
                        # Adapter observed a pre-dispatch snapshot from this exact
                        # native scope. Do not charge missing prior-owner work to
                        # the new owner, even when the prior end was incomplete.
                        inherited, invalid = normalize(
                            event.model_copy(update={"counters": event.inherited})
                        )
                        if invalid or any(
                            getattr(inherited, k) is not None
                            and getattr(baseline, k) is not None
                            and getattr(inherited, k) < getattr(baseline, k)
                            for k in COMPONENTS
                        ):
                            disposition = "invalid-baseline"
                        else:
                            baseline = inherited
                    elif (
                        prior is None
                        or execution_record(prior, prior_observations).coverage != "complete"
                    ):
                        disposition = "unknown-resume-boundary"
                if checkpoint.get("blocked"):
                    disposition = "unresolved-counter-boundary"
                elif checkpoint["execution"] > execution:
                    disposition = "superseded-owner"
                elif (
                    event.order is not None
                    and checkpoint.get("order") is not None
                    and event.order < checkpoint["order"]
                    and checkpoint["execution"] == execution
                ):
                    disposition = "reordered"
                elif disposition == "accepted" and any(
                    getattr(normalized, k) is not None
                    and getattr(baseline, k) is not None
                    and getattr(normalized, k) < getattr(baseline, k)
                    for k in COMPONENTS
                ):
                    disposition = "unknown-reset-or-regression"
            elif event.inherited is not None:
                baseline, baseline_error = normalize(
                    event.model_copy(update={"counters": event.inherited})
                )
                if baseline_error:
                    disposition = "invalid-baseline"
            elif event.baseline in ("fresh", "explicit-reset"):
                baseline = Tokens(**dict.fromkeys(COMPONENTS, 0))
            else:
                disposition = "unknown-baseline"
            if db.execute(
                "SELECT 1 FROM usage_closed_scopes WHERE workspace_id=? AND series=?",
                (workspace, series),
            ).fetchone():
                disposition = "deleted-scope-boundary"
            if "native-session-owner-conflict" in json.loads(owner["reasons"]):
                disposition = "native-session-owner-conflict"
            if baseline and disposition == "accepted":
                differences = {
                    key: getattr(normalized, key) - getattr(baseline, key)
                    if getattr(normalized, key) is not None and getattr(baseline, key) is not None
                    else None
                    for key in COMPONENTS
                }
                if any(x is not None and x < 0 for x in differences.values()):
                    disposition = "invalid-baseline"
                else:
                    delta = Tokens.model_validate(differences)
                    if delta.total is None:
                        disposition = "missing-counters"
            if checkpoint and disposition == "unknown-reset-or-regression":
                checkpoint["blocked"] = True
                db.execute(
                    "UPDATE usage_checkpoints SET data=? WHERE workspace_id=? AND series=?",
                    (json.dumps(checkpoint), workspace, series),
                )
            if disposition not in ("accepted", "reordered"):
                _reason(db, execution, disposition)
            db.execute(
                "INSERT INTO "
                "usage_observations(execution,series,event_id,fingerprint,received_at,"
                "data,baseline,delta,disposition) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    execution,
                    series,
                    event.event_id,
                    fingerprint,
                    now(),
                    payload,
                    baseline.model_dump_json() if baseline else None,
                    delta.model_dump_json(),
                    disposition,
                ),
            )
            # Unknown inherited history is retained as evidence, not established as a
            # baseline. Only a proven fresh/reset or explicit baseline restores accounting.
            if disposition == "accepted":
                db.execute(
                    "INSERT INTO usage_checkpoints VALUES(?,?,?,?) ON "
                    "CONFLICT(workspace_id,series) DO UPDATE SET "
                    "execution=excluded.execution,data=excluded.data",
                    (
                        workspace,
                        series,
                        execution,
                        json.dumps(
                            {
                                "execution": execution,
                                "counters": normalized.model_dump(),
                                "order": event.order,
                            }
                        ),
                    ),
                )

    def rebuild(self) -> None:
        """Rebuild disposable high-water checkpoints from retained accepted evidence."""
        with self.workspace.connection(write=True, notify=False) as db:
            db.execute("DELETE FROM usage_checkpoints")
            for row in db.execute(
                "SELECT o.*,e.workspace_id FROM usage_observations o JOIN "
                "usage_executions e ON e.number=o.execution ORDER BY o.number"
            ):
                if row["disposition"] == "unknown-reset-or-regression":
                    db.execute(
                        "UPDATE usage_checkpoints SET data=json_set(data,'$.blocked',json('true')) "
                        "WHERE workspace_id=? AND series=?",
                        (row["workspace_id"], row["series"]),
                    )
                    continue
                if row["disposition"] != "accepted":
                    continue
                event = Consumption.model_validate_json(row["data"])
                normalized, _ = normalize(event)
                db.execute(
                    "INSERT INTO usage_checkpoints VALUES(?,?,?,?) ON "
                    "CONFLICT(workspace_id,series) DO UPDATE SET "
                    "execution=excluded.execution,data=excluded.data",
                    (
                        row["workspace_id"],
                        row["series"],
                        row["execution"],
                        json.dumps(
                            {
                                "execution": row["execution"],
                                "counters": normalized.model_dump(),
                                "order": event.order,
                            }
                        ),
                    ),
                )
