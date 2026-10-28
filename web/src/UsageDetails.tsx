import { useState } from "react";
import { ContentStack, Disclosure } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { WorkspaceLink } from "./WorkspaceLink";
import { Timestamp } from "./Timestamp";
import { requestHref } from "./requestLinks";
import {
  usageNumber,
  usageLabel,
  usageCoverage,
  usageEvidencePath,
  useUsagePage,
  type Tokens,
  type UsageExecution,
  type UsageEvidencePage,
  type UsageView,
} from "./usage";
import type { components } from "./api-schema";

export function UsageReadState({
  resource,
  name,
}: {
  resource: {
    error: string;
    loading: boolean;
    data: unknown;
    retry: () => void;
  };
  name: string;
}) {
  return (
    <>
      {resource.loading && !resource.data && (
        <p role="status">Loading {name}…</p>
      )}
      {resource.error && (
        <p role="status">
          Could not refresh {name}.
          {resource.data ? " Showing the last loaded values." : ""}
        </p>
      )}
      <ResourceRetry resources={[resource]}>Retry {name}</ResourceRetry>
    </>
  );
}

export function UsageTokenDetails({ tokens }: { tokens: Tokens }) {
  return (
    <ContentStack space="tight">
      <p>
        Input: {usageNumber(tokens.input)} · Output:{" "}
        {usageNumber(tokens.output)}
      </p>
      <p className="detail-metadata">
        Within input: cache read {usageNumber(tokens.cache_read)}, cache write{" "}
        {usageNumber(tokens.cache_write)}. Within output: reasoning{" "}
        {usageNumber(tokens.reasoning)}. These subsets are not added again.
      </p>
    </ContentStack>
  );
}

export function UsageCoverageDetails({ view }: { view: UsageView }) {
  const t = view.totals;
  return (
    <ContentStack>
      {view.limit_reached && (
        <p>
          Only a bounded subtotal is available. Narrow the period or filters for
          a complete selection.
        </p>
      )}
      <p>
        {t.complete} final for scope · {t.partial} partial · {t.unavailable}{" "}
        unavailable
      </p>
      <p>
        Coverage counts executions, not a percentage of tokens captured. Final
        means reconciled for the reported scope, not account-wide completeness.
      </p>
      <p>
        {t.recorded_intervals} recorded intervals · {t.live_intervals} live ·{" "}
        {t.unknown_intervals} unknown
      </p>
      <p>
        Recorded execution time includes tools and waits. Parallel executions
        can exceed elapsed task time. Live and unknown endpoints are not
        extended into this total.
      </p>
      <p className="detail-metadata">
        Collection started <ExactUsageTime value={view.collection_started_at} />{" "}
        · {t.legacy} legacy attempts · {t.unknown_starts} unknown starts
      </p>
      {t.reasons.length > 0 && (
        <p>Gaps: {t.reasons.map(usageLabel).join("; ")}</p>
      )}
      <UsageTokenDetails tokens={t.tokens} />
      <ContentStack aria-label="Harness coverage">
        {view.contracts.map((contract) => (
          <ContentStack key={contract.harness} space="tight">
            <p>
              {contract.harness}:{" "}
              {contract.supported ? "Supported scope" : "Unavailable"}
            </p>
            <p>
              {contract.scope}. {contract.limitation}
            </p>
            {contract.contract && (
              <p className="detail-metadata">
                {contract.contract} · native{" "}
                {contract.native_version ?? "version not reported"}
              </p>
            )}
          </ContentStack>
        ))}
      </ContentStack>
    </ContentStack>
  );
}

export function UsageBreakdowns({ view }: { view: UsageView }) {
  return (
    <Disclosure summary="Role and source breakdowns">
      <p>
        Alternate views of the same total; do not add these groups together.
      </p>
      {(["role", "source"] as const).map((dimension) => (
        <ContentStack key={dimension}>
          <p>{dimension === "role" ? "Actor role" : "Execution source"}</p>
          <ul>
            {view.breakdowns
              .filter((b) => b.dimension === dimension)
              .map((b) => (
                <li key={b.key}>
                  {usageLabel(b.key ?? "Unknown")}:{" "}
                  {usageNumber(b.tokens.total)} tokens · {b.attempts} attempts
                </li>
              ))}
          </ul>
        </ContentStack>
      ))}
    </Disclosure>
  );
}

export function ExactUsageTime({ value }: { value: string | null }) {
  return value ? (
    <Timestamp date={value} format="absolute" />
  ) : (
    <>Not recorded</>
  );
}

export function UsageExecutionList({
  items,
  workspaceId,
  refresh,
}: {
  items: UsageExecution[];
  workspaceId: string;
  refresh: unknown;
}) {
  return (
    <ol className="usage-executions" aria-label="Counted executions">
      {items.map((item) => (
        <li key={`${item.source}:${item.source_id}`}>
          <UsageExecutionDetails
            execution={item}
            workspaceId={workspaceId}
            refresh={refresh}
          />
        </li>
      ))}
    </ol>
  );
}

export function UsageExecutionDetails({
  execution,
  workspaceId,
  refresh,
}: {
  execution: UsageExecution;
  workspaceId: string;
  refresh: unknown;
}) {
  const [opened, setOpened] = useState(false);
  return (
    <Disclosure
      onToggle={(event) => setOpened(event.currentTarget.open)}
      summary={
        <>
          {usageLabel(execution.role)} · {usageLabel(execution.purpose)} ·{" "}
          {usageLabel(execution.outcome)}
          <span className="detail-metadata block">
            {usageNumber(execution.tokens.total)} tokens ·{" "}
            {usageCoverage(execution.coverage)}
          </span>
        </>
      }
    >
      {opened && (
        <ExecutionEvidence
          key={execution.source_id}
          execution={execution}
          workspaceId={workspaceId}
          refresh={refresh}
        />
      )}
    </Disclosure>
  );
}

function ExecutionEvidence({
  execution,
  workspaceId,
  refresh,
}: {
  execution: UsageExecution;
  workspaceId: string;
  refresh: unknown;
}) {
  const path = usageEvidencePath(workspaceId, execution);
  const resource = useUsagePage<UsageEvidencePage>(path, refresh);
  const current = resource.data?.execution ?? execution;
  return (
    <ContentStack
      className="usage-evidence"
      role="region"
      aria-label="Execution numeric evidence"
      tabIndex={0}
    >
      <UsageReadState resource={resource} name="execution evidence" />
      <p>
        Source: {usageLabel(current.source)} · {current.source_id}
      </p>
      {current.parent_id && <p>Parent: {current.parent_id}</p>}
      {current.subtask_id && <p>Subtask: {current.subtask_id}</p>}
      <p>
        {current.harness ?? "Harness unknown"} · Reported model:{" "}
        {current.observed_models
          .map((model) => model ?? "Unknown")
          .join(", ") || "Not reported"}
      </p>
      <p className="detail-metadata">
        Requested model: {current.requested_model ?? "Not recorded"} ·{" "}
        {current.dispatch_count} dispatches · {current.observation_count}{" "}
        observations
      </p>
      <p>
        Timing: {usageLabel(current.timing)}. Start:{" "}
        <ExactUsageTime value={current.started_at} />. End:{" "}
        <ExactUsageTime value={current.ended_at} />.
      </p>
      {current.reasons.length > 0 && (
        <p>Gaps: {current.reasons.map(usageLabel).join("; ")}</p>
      )}
      <UsageTokenDetails tokens={current.tokens} />
      <div className="actions">
        {current.task_id && (
          <WorkspaceLink
            to={`/workspaces/${encodeURIComponent(workspaceId)}/tasks/${encodeURIComponent(current.task_id)}`}
          >
            Open task
          </WorkspaceLink>
        )}
        {current.source === "run" && (
          <WorkspaceLink
            to={requestHref(workspaceId, "attempt", current.source_id)}
          >
            Open exact attempt
          </WorkspaceLink>
        )}
        <a href={`/api/${path}`} target="_blank" rel="noreferrer">
          Exact numeric evidence (JSON)
        </a>
      </div>
      {resource.data?.items.length === 0 && (
        <p>No consumption observations retained for this execution.</p>
      )}
      {resource.data?.items.map((item) => (
        <Observation key={item.number} item={item} />
      ))}
      {resource.data?.next_after != null && (
        <LoadMore
          disabled={resource.loadingMore}
          onClick={() => void resource.older()}
        >
          More observations
        </LoadMore>
      )}
    </ContentStack>
  );
}

function Observation({
  item,
}: {
  item: components["schemas"]["UsageEvidence"];
}) {
  const o = item.observation;
  return (
    <Disclosure
      summary={`Observation ${item.number} · ${usageLabel(item.disposition)} · ${usageNumber(item.delta.total)} tokens counted`}
    >
      <p>
        Received: <ExactUsageTime value={item.received_at} />. Source time:{" "}
        <ExactUsageTime value={o.source_time} />. Source order:{" "}
        {o.order ?? "Unknown"}.
      </p>
      <p>
        Scope: {usageLabel(o.scope)}. Contract: {o.contract}. Native:{" "}
        {o.native_version ?? "Unknown"}. Adapter: {o.adapter_version}.
      </p>
      <p>
        Session: {o.session_id} · Epoch: {o.epoch} · Event: {o.event_id} ·
        Model: {o.model ?? "Not reported"}
      </p>
      <p>
        Native scope ID: {o.native_scope_id ?? "Not reported"} · Dispatch:{" "}
        {o.dispatch_id ?? "Not reported"}
      </p>
      <p>
        Baseline: {usageLabel(o.baseline)} ·{" "}
        {o.final ? "Final report" : "Interim report"}
        {o.gap ? ` · Gap: ${usageLabel(o.gap)}` : ""}
      </p>
      {o.uncertain_components.length > 0 && (
        <p>
          Uncertain counters:{" "}
          {o.uncertain_components.map(usageLabel).join(", ")}. Native defaults
          or omissions do not establish measured zero.
        </p>
      )}
      {o.limitations.length > 0 && (
        <p>Scope limitations: {o.limitations.map(usageLabel).join("; ")}</p>
      )}
      <div
        className="usage-table-scroll"
        role="region"
        aria-label="Numeric provenance"
        tabIndex={0}
      >
        <table className="usage-token-table">
          <caption>
            Raw counters, accounting baseline and normalized counted delta
          </caption>
          <thead>
            <tr>
              <th scope="col">Tokens</th>
              <th scope="col">Raw</th>
              <th scope="col">Baseline</th>
              <th scope="col">Counted delta</th>
            </tr>
          </thead>
          <tbody>
            {(
              [
                "input",
                "output",
                "cache_read",
                "cache_write",
                "reasoning",
                "total",
              ] as const
            ).map((key) => (
              <tr key={key}>
                <th scope="row">{usageLabel(key)}</th>
                <td>{usageNumber(o.counters[key])}</td>
                <td>{usageNumber(item.baseline?.[key])}</td>
                <td>{usageNumber(item.delta[key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        {o.semantics === "disjoint-cache"
          ? "Raw input excludes cache; normalized input includes it."
          : "Raw input includes cache."}{" "}
        Reasoning is within output. Only the counted delta contributes to
        totals; rejected or repeated evidence adds nothing.
      </p>
      {o.inherited && (
        <Disclosure summary="Inherited counters">
          <UsageTokenDetails tokens={o.inherited} />
          <p>Total: {usageNumber(o.inherited.total)}</p>
        </Disclosure>
      )}
      <p>
        {item.timing === "execution-bounded"
          ? "Consumption is bounded by the execution interval, not assigned to an exact instant."
          : "Consumption timing is unknown."}{" "}
        Interval: <ExactUsageTime value={item.interval_start} /> to{" "}
        <ExactUsageTime value={item.interval_end} />. Receipt time may follow
        consumption; totals are never spread proportionally across hours.
      </p>
    </Disclosure>
  );
}
