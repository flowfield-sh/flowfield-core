import { useEffect, useRef, type RefObject } from "react";
import {
  Bar,
  ComposedChart,
  Line,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  useActiveTooltipLabel,
} from "recharts";
import {
  costAmount,
  usageCost,
  usageDuration,
  usageNumber,
  type UsageView,
} from "./usage";
import { bucketLabel } from "./usagePeriod";

export type UsageMetric = "tokens" | "duration" | "cost";
const roleNames: Record<string, string> = {
  coordinator: "Coordinator",
  worker: "Worker",
  reviewer: "Reviewer",
};
const harnessNames: Record<string, string> = {
  codex: "Codex",
  "claude-code": "Claude Code",
  pi: "Pi",
};
export default function UsageChart({
  view,
  metric,
  select,
}: {
  view: UsageView;
  metric: UsageMetric;
  select: (start: string, end: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const zoomable = view.filters.group !== "minute";
  const dimension = view.filters.dimension;
  const series = view.series.map((series, index) => ({
    ...series,
    field: `series${index}`,
    label: series.other
      ? "Other"
      : series.key == null
        ? "Unreported"
        : dimension === "role"
          ? (roleNames[series.key] ?? series.key)
          : dimension === "harness"
            ? (harnessNames[series.key] ?? series.key)
            : series.key,
    color: `var(--usage-series-${
      dimension === "role"
        ? ["coordinator", "worker", "reviewer"].indexOf(series.key ?? "") + 1 ||
          8
        : dimension === "harness"
          ? ["codex", "claude-code", "pi"].indexOf(series.key ?? "") + 1 || 8
          : (index % 8) + 1
    })`,
  }));
  const data = view.buckets.map((bucket, index) => ({
    start: bucket.start,
    end: bucket.end,
    ...Object.fromEntries(
      series.map((series) => [
        series.field,
        metric === "tokens"
          ? series.points[index]?.tokens.total
          : metric === "cost"
            ? costAmount(series.points[index]?.cost)
            : series.points[index]?.elapsed_seconds,
      ]),
    ),
  }));
  const maximumDuration = Math.max(
    0,
    ...view.series.flatMap((series) =>
      series.points.map((point) => point.elapsed_seconds ?? 0),
    ),
  );
  const step =
    [
      1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600,
      43200, 86400,
    ].find((step) => maximumDuration / step <= 4) ?? 86400;
  const durationTicks = Array.from(
    { length: Math.max(1, Math.ceil(maximumDuration / step)) + 1 },
    (_, index) => index * step,
  );
  const format = (value: number | null | undefined) =>
    metric === "tokens"
      ? usageNumber(value)
      : metric === "cost"
        ? usageCost(value)
        : usageDuration(value ?? null);
  return (
    <div
      className="usage-chart"
      ref={root}
      role="region"
      aria-label={`${metric === "tokens" ? "Token usage" : metric === "cost" ? "Cost" : "Duration"} over time`}
    >
      <ResponsiveContainer width="100%" height={260} minWidth={0}>
        <ComposedChart
          data={data}
          accessibilityLayer
          style={{ cursor: zoomable ? "pointer" : undefined }}
          aria-label={
            zoomable
              ? "Use arrow keys to inspect intervals and Enter to zoom in"
              : "Use arrow keys to inspect intervals"
          }
          onClick={(state) => {
            if (!zoomable || state.activeIndex == null) return;
            const bucket = view.buckets[Number(state.activeIndex)];
            if (bucket) select(bucket.start, bucket.end);
          }}
          margin={{ top: 8, right: 24, left: 0, bottom: 8 }}
        >
          <ChartKeyboard
            root={root}
            view={view}
            select={zoomable ? select : undefined}
          />
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="start"
            minTickGap={32}
            interval="preserveStartEnd"
            tickFormatter={(value: string) =>
              bucketLabel(
                value,
                view.filters.timezone!,
                view.filters.group!,
                true,
              )
            }
            tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          />
          <YAxis
            width={80}
            ticks={metric === "duration" ? durationTicks : undefined}
            domain={
              metric === "duration" ? [0, durationTicks.at(-1)!] : [0, "auto"]
            }
            tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
            tickFormatter={(n: number) =>
              metric === "tokens"
                ? new Intl.NumberFormat(undefined, {
                    notation: "compact",
                  }).format(n)
                : metric === "cost"
                  ? usageCost(n, true)
                  : n >= 3600
                    ? `${n / 3600}h`
                    : n >= 60
                      ? `${n / 60}m`
                      : `${n}s`
            }
          />
          <Tooltip
            isAnimationActive={false}
            filterNull={false}
            cursor={
              metric !== "duration"
                ? { fill: "var(--muted)" }
                : { stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }
            }
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="usage-chart-tooltip">
                  <p>
                    {bucketLabel(
                      String(label),
                      view.filters.timezone!,
                      view.filters.group!,
                    )}
                  </p>
                  {payload.map((item) => (
                    <p key={String(item.dataKey)}>
                      <span
                        className="usage-series-dot"
                        style={{ background: item.color }}
                      />
                      {item.name}:{" "}
                      {format(
                        typeof item.value === "number" ? item.value : null,
                      )}
                      {metric === "tokens" ? " tokens" : ""}
                    </p>
                  ))}
                </div>
              ) : null
            }
          />
          {series.map((series) =>
            metric === "duration" ? (
              <Line
                key={series.field}
                dataKey={series.field}
                name={series.label}
                stroke={series.color}
                strokeWidth={2}
                type="linear"
                dot={{ r: 2 }}
                activeDot={{ r: 5 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ) : (
              <Bar
                key={series.field}
                dataKey={series.field}
                name={series.label}
                fill={series.color}
                stackId={metric}
                maxBarSize={40}
                isAnimationActive={false}
                cursor={zoomable ? "pointer" : undefined}
              />
            ),
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <ul className="usage-legend" aria-label="Chart legend">
        {series.map((series) => (
          <li key={series.field}>
            <span
              className="usage-series-dot"
              style={{ background: series.color }}
            />
            {series.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

// Recharts owns arrow-key navigation. Enter uses that same active interval,
// without adding every bar to the page's Tab sequence.
function ChartKeyboard({
  root,
  view,
  select,
}: {
  root: RefObject<HTMLDivElement | null>;
  view: UsageView;
  select?: (start: string, end: string) => void;
}) {
  const label = useActiveTooltipLabel();
  useEffect(() => {
    const element = root.current;
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !select) return;
      const bucket = view.buckets.find((bucket) => bucket.start === label);
      if (!bucket) return;
      event.preventDefault();
      select(bucket.start, bucket.end);
    };
    element?.addEventListener("keydown", keydown);
    return () => element?.removeEventListener("keydown", keydown);
  }, [label, root, select, view.buckets]);
  return null;
}
