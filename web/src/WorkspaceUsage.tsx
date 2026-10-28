import { lazy, Suspense, useEffect, useState } from "react";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { UsageMetrics, UsageReadState } from "./UsageMetrics";
import type { WorkspaceBoard } from "./workspace";
import { useUsagePage } from "./usage";
import { defaultUsageQuery, todayPeriod, localDate } from "./usagePeriod";
const UsageChart = lazy(() => import("./UsageChart"));

// Retain older deep-link filters, but keep routine controls to date and task.
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
  function change(changes: Record<string, string>) {
    const next = new URLSearchParams(canonical);
    for (const [key, value] of Object.entries(changes))
      if (value) next.set(key, value);
      else next.delete(key);
    navigate(`${location.pathname}?${next}`);
  }
  const query = new URLSearchParams();
  for (const key of ["start", "end", "timezone", "group", ...filterKeys])
    if (params.get(key)) query.set(key, params.get(key)!);
  const resource = useUsagePage(
    `workspaces/${encodeURIComponent(workspaceId)}/usage?${query}`,
    refresh,
  );
  const timezone = params.get("timezone")!;
  let validDates = false;
  try {
    localDate(Date.parse(params.get("start")!), timezone);
    localDate(Date.parse(params.get("end")!), timezone);
    validDates = true;
  } catch {
    // Invalid saved links still reach the API's normal error/recovery surface.
  }
  const tasks = board.tasks;
  const taskId = params.get("task_id") || "";
  const hiddenFilters = filterKeys.filter(
    (key) => key !== "task_id" && params.has(key),
  );
  return (
    <section className="workspace-usage p-5" aria-label="Workspace usage">
      <ContentStack>
        <DetailHeading title="Usage" titleAs="h2" />
        <div className="usage-controls">
          <NativeSelect
            aria-label="Usage period"
            size="sm"
            value={params.get("period")!}
            onChange={(e) => {
              const preset = e.target.value;
              if (preset === "custom") {
                change({ period: preset });
                return;
              }
              const zone = validDates ? timezone : defaults.get("timezone")!;
              const period = todayPeriod(zone);
              const days = preset === "month" ? 30 : preset === "week" ? 7 : 1;
              for (let day = 1; day < days; day++)
                period.start = todayPeriod(
                  zone,
                  Date.parse(period.start) - 1,
                ).start;
              change({
                ...period,
                timezone: zone,
                period: preset,
                group: days === 1 ? "hour" : "day",
              });
            }}
          >
            <option value="today">Today</option>
            <option value="week">Last 7 days</option>
            <option value="month">Last 30 days</option>
            <option value="custom">Custom dates</option>
          </NativeSelect>
          <NativeSelect
            className="usage-task-select"
            aria-label="Usage task"
            size="sm"
            value={taskId}
            onChange={(e) => change({ task_id: e.target.value })}
          >
            <option value="">All tasks &amp; Coordinator</option>
            {taskId && !tasks.some((task) => task.id === taskId) && (
              <option value={taskId}>Selected task</option>
            )}
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.key} · {task.title}
                {task.archived ? " (archived)" : ""}
              </option>
            ))}
          </NativeSelect>
        </div>
        {params.get("period") === "custom" && validDates && (
          <DateRange
            key={`${params.get("start")}/${params.get("end")}/${timezone}`}
            start={params.get("start")!}
            end={params.get("end")!}
            timezone={timezone}
            apply={change}
          />
        )}
        {!!hiddenFilters.length && (
          <div className="actions">
            <span className="detail-metadata">
              Filtered:{" "}
              {hiddenFilters
                .map((key) => `${key.replaceAll("_", " ")}: ${params.get(key)}`)
                .join(" · ")}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                change(
                  Object.fromEntries(hiddenFilters.map((key) => [key, ""])),
                )
              }
            >
              Clear filters
            </Button>
          </div>
        )}
        <UsageReadState resource={resource} name="usage" />
        {resource.error && !validDates && (
          <div className="actions">
            <Button
              size="sm"
              variant="outline"
              onClick={() => change(Object.fromEntries(defaultUsageQuery()))}
            >
              Reset dates
            </Button>
          </div>
        )}
        {resource.data &&
          (resource.data.totals.attempts === 0 ? (
            <p>No usage in this period.</p>
          ) : (
            <>
              <UsageMetrics view={resource.data} task={!!taskId} />
              <Suspense fallback={<p role="status">Loading chart…</p>}>
                <UsageChart view={resource.data} />
              </Suspense>
            </>
          ))}
      </ContentStack>
    </section>
  );
}

function DateRange({
  start,
  end,
  timezone,
  apply,
}: {
  start: string;
  end: string;
  timezone: string;
  apply: (changes: Record<string, string>) => void;
}) {
  const [from, setFrom] = useState(() =>
    localDate(Date.parse(start), timezone),
  );
  const [through, setThrough] = useState(() =>
    localDate(Date.parse(end) - 1, timezone),
  );
  const [error, setError] = useState("");
  function boundary(date: string, last: boolean) {
    // Noon UTC falls within the selected date in almost all zones; find the
    // matching local day explicitly for extreme offsets before using DST bounds.
    let probe = Date.parse(`${date}T12:00:00Z`);
    while (localDate(probe, timezone) < date) probe += 3600000;
    while (localDate(probe, timezone) > date) probe -= 3600000;
    const period = todayPeriod(timezone, probe);
    return last ? period.end : period.start;
  }
  return (
    <form
      className="usage-date-range"
      aria-label="Custom usage dates"
      onSubmit={(event) => {
        event.preventDefault();
        if (!from || !through || through < from) {
          setError("Choose an end date on or after the start date.");
          return;
        }
        const left = boundary(from, false),
          right = boundary(through, true);
        if (Date.parse(right) - Date.parse(left) > 366 * 86400000) {
          setError("Choose up to one year.");
          return;
        }
        apply({
          start: left,
          end: right,
          group: from === through ? "hour" : "day",
        });
      }}
    >
      <label>
        From
        <Input
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          required
        />
      </label>
      <label>
        Through
        <Input
          type="date"
          value={through}
          onChange={(e) => setThrough(e.target.value)}
          required
        />
      </label>
      <Button size="sm" variant="outline">
        Apply dates
      </Button>
      <span className="detail-metadata">{timezone}</span>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
