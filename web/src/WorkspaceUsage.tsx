import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  useId,
  type ChangeEvent,
} from "react";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ContentStack, DetailSection } from "./DetailLayout";
import { UsageMetrics, UsageReadState } from "./UsageMetrics";
import { MetricsTasks } from "./MetricsTasks";
import type { WorkspaceBoard } from "./workspace";
import { useUsagePage } from "./usage";
import {
  localDateTime,
  parseLocalDateTime,
  metricsQuery,
  periods,
  presetRange,
  type Period,
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
  const controlId = useId();
  const [openedAt] = useState(Date.now);
  const canonical = useMemo(
    () => metricsQuery(location.search, openedAt).toString(),
    [location.search, openedAt],
  );
  const params = new URLSearchParams(canonical);
  useEffect(() => {
    if (new URLSearchParams(location.search).toString() !== canonical)
      navigate(`${location.pathname}?${canonical}`, true);
  }, [canonical, location.pathname, location.search, navigate]);
  function change(changes: Record<string, string>) {
    const next = new URLSearchParams(canonical);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    navigate(`${location.pathname}?${metricsQuery(next.toString(), openedAt)}`);
  }
  function handlePeriodChange(event: ChangeEvent<HTMLSelectElement>) {
    const period = event.target.value as Period;
    change(
      period === "custom"
        ? { period }
        : { ...presetRange(period, Date.now()), period },
    );
  }
  const query = new URLSearchParams();
  for (const key of [
    "start",
    "end",
    "timezone",
    "group",
    "dimension",
    ...filterKeys,
  ])
    if (params.get(key)) query.set(key, params.get(key)!);
  const resource = useUsagePage(
    `workspaces/${encodeURIComponent(workspaceId)}/usage?${query}`,
    refresh,
  );
  const taskId = params.get("task_id") || "";
  const activity =
    taskId || (params.get("role") === "coordinator" ? "coordinator" : "all");
  const selectedTask = board.tasks.find((task) => task.id === taskId);
  const activityTitle =
    activity === "all"
      ? "All activity"
      : activity === "coordinator"
        ? "Coordinator"
        : selectedTask
          ? `${selectedTask.key} · ${selectedTask.title}`
          : "Selected task";
  const hiddenFilters = filterKeys.filter(
    (key) =>
      key !== "task_id" &&
      !(key === "role" && activity === "coordinator") &&
      params.has(key),
  );
  const start = params.get("start") ?? "",
    end = params.get("end") ?? "";
  return (
    <section className="workspace-usage" aria-label="Workspace metrics">
      <ContentStack space="settings">
        <ContentStack space="settings">
          <div className="usage-controls">
            <div className="metrics-filter">
              <label
                className="detail-metadata"
                htmlFor={`${controlId}-period`}
              >
                Period
              </label>
              <NativeSelect
                id={`${controlId}-period`}
                aria-label="Metrics period"
                size="sm"
                value={params.get("period")!}
                onChange={handlePeriodChange}
              >
                {periods.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="metrics-filter metrics-activity">
              <label
                className="detail-metadata"
                htmlFor={`${controlId}-activity`}
              >
                Activity
              </label>
              <Tooltip>
                <TooltipTrigger asChild>
                  <NativeSelect
                    id={`${controlId}-activity`}
                    aria-label="Metrics activity"
                    size="sm"
                    value={activity}
                    className="truncate"
                    onChange={(e) =>
                      change({
                        task_id: ["all", "coordinator"].includes(e.target.value)
                          ? ""
                          : e.target.value,
                        role:
                          e.target.value === "coordinator" ? "coordinator" : "",
                        unassigned: "",
                      })
                    }
                  >
                    <option value="all">All activity</option>
                    <option value="coordinator">Coordinator</option>
                    {taskId && !selectedTask && (
                      <option value={taskId}>Selected task</option>
                    )}
                    {board.tasks.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.key} · {task.title}
                        {task.archived ? " (archived)" : ""}
                      </option>
                    ))}
                  </NativeSelect>
                </TooltipTrigger>
                <TooltipContent>{activityTitle}</TooltipContent>
              </Tooltip>
            </div>
            <div className="metrics-filter">
              <label className="detail-metadata" htmlFor={`${controlId}-group`}>
                Group by
              </label>
              <Tooltip>
                <TooltipTrigger asChild>
                  <NativeSelect
                    id={`${controlId}-group`}
                    aria-label="Chart grouping"
                    size="sm"
                    value={params.get("dimension")!}
                    onChange={(e) => change({ dimension: e.target.value })}
                  >
                    <option value="role">Agent type</option>
                    <option value="harness">Harness</option>
                    <option value="model">Model</option>
                  </NativeSelect>
                </TooltipTrigger>
                {params.get("dimension") === "model" && (
                  <TooltipContent>
                    Groups by the model selected for each run.
                  </TooltipContent>
                )}
              </Tooltip>
            </div>
          </div>
          <DateRange
            key={`${start}/${end}`}
            start={start}
            end={end}
            apply={(range) => change({ ...range, period: "custom" })}
          />
        </ContentStack>
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
        <UsageReadState resource={resource} name="metrics" />
        {resource.error && (!localDateTime(start) || !localDateTime(end)) && (
          <div className="actions">
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                change({ ...presetRange("today", Date.now()), period: "today" })
              }
            >
              Reset dates
            </Button>
          </div>
        )}
        {resource.data &&
          (resource.data.totals.attempts === 0 ? (
            <p>No metrics in this period.</p>
          ) : (
            <>
              <UsageMetrics view={resource.data} />
              <div className="metrics-sections">
                <Suspense fallback={<p role="status">Loading charts…</p>}>
                  {(["tokens", "duration", "cost"] as const).map((metric) => (
                    <DetailSection
                      key={metric}
                      title={
                        metric === "tokens"
                          ? "Tokens"
                          : metric === "cost"
                            ? "Cost"
                            : "Duration"
                      }
                    >
                      <UsageChart
                        view={resource.data!}
                        metric={metric}
                        select={(start, end) =>
                          change({ start, end, period: "custom" })
                        }
                      />
                    </DetailSection>
                  ))}
                </Suspense>
                <DetailSection title="Tasks">
                  <MetricsTasks
                    key={query.toString()}
                    workspaceId={workspaceId}
                    view={resource.data}
                    sort={params.get("sort") || "tokens-desc"}
                    change={(sort) => change({ sort })}
                  />
                </DetailSection>
              </div>
            </>
          ))}
      </ContentStack>
    </section>
  );
}
function DateRange({
  start,
  end,
  apply,
}: {
  start: string;
  end: string;
  apply: (range: { start: string; end: string }) => void;
}) {
  const [from, setFrom] = useState(() => localDateTime(start));
  const [to, setTo] = useState(() => localDateTime(end));
  const [error, setError] = useState("");
  const dirty = from !== localDateTime(start) || to !== localDateTime(end);
  return (
    <form
      className="usage-date-range detail-panel"
      aria-label="Metrics datetime range"
      onSubmit={(event) => {
        event.preventDefault();
        try {
          const left = parseLocalDateTime(from, start),
            right = parseLocalDateTime(to, end);
          const seconds = (Date.parse(right) - Date.parse(left)) / 1000;
          if (seconds <= 0 || seconds > 366 * 86400)
            throw new Error("Choose an end after the start, within one year.");
          setError("");
          apply({ start: left, end: right });
        } catch (error) {
          setError((error as Error).message);
        }
      }}
    >
      <label>
        <span className="detail-metadata">From</span>
        <Input
          type="datetime-local"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          required
        />
      </label>
      <label>
        <span className="detail-metadata">To</span>
        <Input
          type="datetime-local"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          required
        />
      </label>
      <Button variant="outline" disabled={!dirty}>
        Apply
      </Button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
