import { lazy, Suspense, useEffect, useState } from "react";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { ContentStack, DetailHeading, Disclosure } from "./DetailLayout";
import {
  UsageCoverageDetails,
  UsageExecutionList,
  UsageReadState,
} from "./UsageDetails";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { LoadMore } from "./LoadMore";
import { WorkspaceLink } from "./WorkspaceLink";
import type { WorkspaceBoard } from "./workspace";
import {
  usageCoverage,
  usageDuration,
  usageLabel,
  usageNumber,
  useUsagePage,
  type UsageView,
} from "./usage";
import {
  bucketLabel,
  defaultUsageQuery,
  todayPeriod,
  utcInput,
} from "./usagePeriod";
const UsageChart = lazy(() => import("./UsageChart"));
const filterKeys = [
  "task_id",
  "project_id",
  "role",
  "purpose",
  "outcome",
  "harness",
  "model",
  "coverage",
  "source",
  "unassigned",
  "unknown_model",
  "related_task_id",
];

const dimensionLabels: Record<string, string> = {
  task: "Task",
  role: "Actor role",
  source: "Execution source",
  harness: "Harness",
  model: "Reported model",
  purpose: "Purpose",
  outcome: "Outcome",
  coverage: "Coverage",
};

export function WorkspaceUsage({
  workspaceId,
  board,
  refresh,
  navigate,
}: {
  workspaceId: string;
  board: WorkspaceBoard;
  refresh: unknown;
  navigate: (url: string, replace?: boolean) => void;
}) {
  const location = useLocation();
  const [defaults] = useState(defaultUsageQuery);
  const params = new URLSearchParams(location.search);
  if (!params.has("period") && (params.has("start") || params.has("end")))
    params.set("period", "custom");
  for (const [key, value] of defaults)
    if (!params.has(key)) params.set(key, value);
  const canonical = params.toString();
  useEffect(() => {
    if (new URLSearchParams(location.search).toString() !== canonical)
      navigate(`${location.pathname}?${canonical}`, true);
  }, [canonical, location.pathname, location.search, navigate]);
  function href(changes: Record<string, string>) {
    const next = new URLSearchParams(canonical);
    for (const [key, value] of Object.entries(changes))
      if (value) next.set(key, value);
      else next.delete(key);
    return `${location.pathname}?${next}`;
  }
  function change(changes: Record<string, string>) {
    navigate(href(changes));
  }
  const query = new URLSearchParams();
  for (const key of ["start", "end", "timezone", "group", ...filterKeys]) {
    if (params.get(key)) query.set(key, params.get(key)!);
  }
  const resource = useUsagePage<UsageView>(
    `workspaces/${encodeURIComponent(workspaceId)}/usage?${query}`,
    refresh,
  );
  const view = resource.data;
  const timezone = params.get("timezone")!;
  const group = params.get("group")!;
  const table = params.get("view") === "table";
  const details = params.get("details") === "1";
  const dimension = params.get("breakdown") || "task";
  const activeFilters = filterKeys.filter((k) => params.has(k)).length;
  const taskName = (id: string | null) => {
    if (!id) return "No task assigned";
    const task = board.tasks.find((t) => t.id === id);
    return task
      ? `${task.key} ${task.title}${task.archived ? " (archived)" : ""}`
      : id;
  };
  const bucketChanges = (start: string, end: string) => ({
    start,
    end,
    period: "custom",
    details: "1",
  });
  return (
    <section className="workspace-usage p-5" aria-label="Workspace usage">
      <ContentStack>
        <DetailHeading title="Usage" titleAs="h2" />
        <div className="actions usage-controls">
          <NativeSelect
            aria-label="Usage period"
            size="sm"
            value={params.get("period")!}
            onChange={(e) => {
              if (e.target.value === "custom") {
                change({ period: "custom", filters: "1" });
                return;
              }
              try {
                const period = todayPeriod(timezone);
                if (e.target.value === "week")
                  for (let day = 1; day < 7; day++)
                    period.start = todayPeriod(
                      timezone,
                      Date.parse(period.start) - 1,
                    ).start;
                change({ ...period, period: e.target.value });
              } catch {
                change({ filters: "1" });
              }
            }}
          >
            <option value="today">Today</option>
            <option value="week">Last 7 days</option>
            <option value="custom">Custom period</option>
          </NativeSelect>
          <NativeSelect
            aria-label="Group usage"
            size="sm"
            value={group}
            onChange={(e) => change({ group: e.target.value })}
          >
            <option value="hour">Hour</option>
            <option value="day">Day</option>
          </NativeSelect>
          <span className="detail-metadata">{timezone}</span>
          <Button
            size="sm"
            variant="outline"
            aria-expanded={params.get("filters") === "1"}
            onClick={() =>
              change({ filters: params.get("filters") === "1" ? "" : "1" })
            }
          >
            Filters{activeFilters ? ` (${activeFilters})` : ""}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              navigate(`${location.pathname}?${defaultUsageQuery()}`)
            }
          >
            Reset
          </Button>
        </div>
        {params.get("filters") === "1" && (
          <UsageFilters
            key={canonical}
            params={params}
            board={board}
            breakdowns={view?.breakdowns ?? []}
            apply={change}
          />
        )}
        <UsageReadState resource={resource} name="workspace usage" />
        {resource.error && (
          <p className="detail-metadata">
            Check the period and timezone in Filters, or reset the selection.
          </p>
        )}
        {view?.filters.start && view.filters.end && (
          <p className="detail-metadata">
            {bucketLabel(view.filters.start, timezone, "hour")} –{" "}
            {bucketLabel(view.filters.end, timezone, "hour")} (end exclusive)
          </p>
        )}
        {view && (
          <>
            {view.totals.attempts === 0 ? (
              <p>
                No recorded executions in this period. Try another period or
                reset filters.
              </p>
            ) : (
              <ContentStack space="tight">
                <p>
                  {view.totals.tokens.total == null
                    ? "Token usage not reported"
                    : `${usageNumber(view.totals.tokens.total)} tokens reported`}{" "}
                  ·{" "}
                  <span className="detail-metadata">
                    {view.limit_reached
                      ? "Partial coverage · read limit reached"
                      : usageCoverage(view.totals.coverage)}
                  </span>
                </p>
                <p className="detail-metadata">
                  {usageDuration(view.totals.recorded_seconds)}
                  {view.totals.live_intervals > 0 && " · Live work pending"}
                  {view.totals.unknown_intervals > 0 &&
                    " · Some timing unknown"}
                </p>
              </ContentStack>
            )}
            {view.limit_reached && (
              <p role="status">
                Only a bounded subtotal is available. Narrow the period or
                filters for a complete selection.
              </p>
            )}
            <div className="actions" aria-label="Usage presentation">
              <Button
                variant={table ? "outline" : "default"}
                size="sm"
                aria-pressed={!table}
                onClick={() => change({ view: "" })}
              >
                Chart
              </Button>
              <Button
                variant={table ? "default" : "outline"}
                size="sm"
                aria-pressed={table}
                onClick={() => change({ view: "table" })}
              >
                Table
              </Button>
            </div>
            <p className="detail-metadata">
              Usage reported in the period. Grouped when reported; consumption
              may span earlier periods.
            </p>
            {table ? (
              <div
                className="usage-table-scroll"
                role="region"
                aria-label="Reported usage by period"
                tabIndex={0}
              >
                <table className="usage-token-table">
                  <caption>
                    Reported usage · {timezone}. Select a period for executions
                    and evidence.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Period starting</th>
                      <th scope="col">Tokens reported</th>
                      <th scope="col">Recorded execution time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.buckets.map((b) => (
                      <tr key={b.start}>
                        <th scope="row">
                          <WorkspaceLink
                            to={href(bucketChanges(b.start, b.end))}
                          >
                            {bucketLabel(b.start, timezone, group)}
                          </WorkspaceLink>
                        </th>
                        <td>{usageNumber(b.tokens.total)}</td>
                        <td>
                          {usageDuration(b.recorded_seconds).replace(
                            " recorded execution time",
                            "",
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Suspense fallback={<p role="status">Loading timeline…</p>}>
                <UsageChart
                  view={view}
                  select={(start, end) => change(bucketChanges(start, end))}
                />
              </Suspense>
            )}
            <Disclosure
              summary="Breakdowns and executions"
              open={details}
              onToggle={(e) => {
                if (e.currentTarget.open !== details)
                  change({ details: e.currentTarget.open ? "1" : "" });
              }}
            >
              {details && (
                <>
                  <p>
                    {view.totals.attempts} execution attempts. Archived tasks
                    and unassigned activity are included. Amounts reflect this
                    selection; opening evidence shows the whole execution.
                  </p>
                  <NativeSelect
                    aria-label="Usage breakdown"
                    value={dimension}
                    onChange={(e) => change({ breakdown: e.target.value })}
                  >
                    {[
                      "task",
                      "role",
                      "source",
                      "harness",
                      "model",
                      "purpose",
                      "outcome",
                    ].map((d) => (
                      <option key={d} value={d}>
                        {dimensionLabels[d]}
                      </option>
                    ))}
                  </NativeSelect>
                  <p className="detail-metadata">
                    Each breakdown is another view of the same usage. Do not add
                    breakdown totals together.
                  </p>
                  <div
                    className="usage-table-scroll"
                    role="region"
                    aria-label="Usage breakdown table"
                    tabIndex={0}
                  >
                    <table className="usage-token-table">
                      <thead>
                        <tr>
                          <th scope="col">
                            {dimensionLabels[dimension] ??
                              usageLabel(dimension)}
                          </th>
                          <th scope="col">Tokens reported</th>
                          <th scope="col">Attempts</th>
                        </tr>
                      </thead>
                      <tbody>
                        {view.breakdowns
                          .filter((b) => b.dimension === dimension)
                          .map((b) => (
                            <tr key={b.key ?? "unknown"}>
                              <th scope="row">
                                <WorkspaceLink
                                  to={href(
                                    b.dimension === "task"
                                      ? {
                                          task_id: b.key ?? "",
                                          unassigned:
                                            b.key == null ? "true" : "",
                                        }
                                      : b.dimension === "model"
                                        ? {
                                            model: b.key ?? "",
                                            unknown_model:
                                              b.key == null ? "true" : "",
                                          }
                                        : { [b.dimension]: b.key ?? "" },
                                  )}
                                >
                                  {b.dimension === "task"
                                    ? taskName(b.key)
                                    : usageLabel(b.key ?? "Not reported")}
                                </WorkspaceLink>
                              </th>
                              <td>{usageNumber(b.tokens.total)}</td>
                              <td>{b.attempts}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                  <UsageExecutionList
                    items={view.items}
                    workspaceId={workspaceId}
                    refresh={refresh}
                  />
                  {view.next_after != null && (
                    <LoadMore
                      disabled={resource.loadingMore}
                      onClick={() => void resource.older()}
                    >
                      More executions
                    </LoadMore>
                  )}
                </>
              )}
            </Disclosure>
            <Disclosure summary="Accounting and coverage">
              <p>
                Chart gaps mean tokens were not reported, not measured zero.
                Table provides exact values and links to evidence.
              </p>
              <UsageCoverageDetails view={view} />
              <p>
                Final-only reports stay in their receipt period. Later higher
                reports add only the new amount. Model-filtered time covers
                matching executions, not time measured per model.
              </p>
            </Disclosure>
          </>
        )}
      </ContentStack>
    </section>
  );
}

function UsageFilters({
  params,
  board,
  breakdowns,
  apply,
}: {
  params: URLSearchParams;
  board: WorkspaceBoard;
  breakdowns: UsageView["breakdowns"];
  apply: (changes: Record<string, string>) => void;
}) {
  const [error, setError] = useState("");
  return (
    <form
      className="content-stack usage-filter-form"
      aria-label="Usage filters"
      onSubmit={(event) => {
        event.preventDefault();
        const data = Object.fromEntries(
          new FormData(event.currentTarget),
        ) as Record<string, string>;
        try {
          new Intl.DateTimeFormat(undefined, {
            timeZone: data.timezone,
          }).format();
        } catch {
          setError("Choose an IANA timezone, for example America/Denver.");
          return;
        }
        // Native date/time fields use UTC explicitly, so repeated DST hours are
        // unambiguous. Preserve exact original instants when only filters change.
        const instant = (key: string) => {
          const entered = `${data[key]}Z`;
          const original = params.get(key)!;
          return Date.parse(entered) ===
            Math.floor(Date.parse(original) / 1000) * 1000
            ? original
            : entered;
        };
        const start = Date.parse(instant("start")),
          end = Date.parse(instant("end"));
        if (
          !Number.isFinite(start + end) ||
          end <= start ||
          end - start > (params.get("group") === "hour" ? 31 : 366) * 86400000
        ) {
          setError(
            "Choose an end after the start; at most 31 days by hour or 366 days by day.",
          );
          return;
        }
        apply({
          ...data,
          start: new Date(start).toISOString(),
          end: new Date(end).toISOString(),
          unassigned: data.task_id === "__unassigned" ? "true" : "",
          task_id: data.task_id === "__unassigned" ? "" : data.task_id,
          unknown_model: data.unknown_model === "true" ? "true" : "",
          model: data.unknown_model === "true" ? "" : data.model,
          period: "custom",
          filters: "",
        });
      }}
    >
      <div className="usage-filter-grid [&_[data-slot=native-select-wrapper]]:w-full">
        {[
          ["start", "Period start (UTC)"],
          ["end", "Period end (UTC, exclusive)"],
        ].map(([key, label]) => (
          <label key={key}>
            {label}
            <Input
              type="datetime-local"
              step="1"
              name={key}
              defaultValue={utcInput(params.get(key))}
              required
            />
          </label>
        ))}
        <label>
          Grouping timezone
          <Input
            name="timezone"
            defaultValue={params.get("timezone")!}
            required
          />
        </label>
        <label>
          Task
          <NativeSelect
            name="task_id"
            aria-label="Task"
            defaultValue={
              params.get("unassigned") === "true"
                ? "__unassigned"
                : (params.get("task_id") ?? "")
            }
          >
            <option value="">All tasks</option>
            <option value="__unassigned">No task assigned</option>
            {params.get("task_id") &&
              !board.tasks.some((t) => t.id === params.get("task_id")) && (
                <option value={params.get("task_id")!}>
                  {params.get("task_id")}
                </option>
              )}
            {board.tasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.key} {t.title}
                {t.archived ? " (archived)" : ""}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label>
          Project
          <NativeSelect
            name="project_id"
            aria-label="Project"
            defaultValue={params.get("project_id") ?? ""}
          >
            <option value="">All projects</option>
            {board.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </NativeSelect>
        </label>
        {[
          ["role", ["worker", "reviewer", "coordinator"]],
          ["coverage", ["complete", "partial", "unavailable"]],
          ["source", ["run", "review_attempt", "coordinator_turn"]],
        ].map(([key, options]) => (
          <label key={key as string}>
            {dimensionLabels[key as string]}
            <NativeSelect
              name={key as string}
              defaultValue={params.get(key as string) ?? ""}
            >
              <option value="">All</option>
              {(options as string[]).map((o) => (
                <option key={o} value={o}>
                  {usageLabel(o)}
                </option>
              ))}
            </NativeSelect>
          </label>
        ))}
        {["purpose", "outcome", "harness", "model"].map((key) => (
          <label key={key}>
            {dimensionLabels[key]}
            <Input
              name={key}
              defaultValue={params.get(key) ?? ""}
              list={`usage-${key}`}
              placeholder="All"
            />
            <datalist id={`usage-${key}`}>
              {Array.from(
                new Set([
                  ...(key === "harness" ? ["claude-code", "codex", "pi"] : []),
                  ...breakdowns
                    .filter((b) => b.dimension === key && b.key)
                    .map((b) => b.key!),
                ]),
              ).map((v) => (
                <option key={v} value={v}>
                  {usageLabel(v)}
                </option>
              ))}
            </datalist>
          </label>
        ))}
      </div>
      <SettingsCheckbox
        name="unknown_model"
        value="true"
        defaultChecked={params.get("unknown_model") === "true"}
      >
        Only models not reported
      </SettingsCheckbox>
      <p className="detail-metadata">
        Dates use UTC. The grouping timezone controls local hour/day labels;
        changing it keeps the same period. Today selects that zone’s local day.
      </p>
      {error && <p role="alert">{error}</p>}
      <div className="actions">
        <Button type="submit" size="sm">
          Apply filters
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => apply({ filters: "" })}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
