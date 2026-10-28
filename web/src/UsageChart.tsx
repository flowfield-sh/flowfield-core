import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { UsageView } from "./usage";
import { usageNumber } from "./usage";
import { bucketLabel } from "./usagePeriod";

export default function UsageChart({ view }: { view: UsageView }) {
  const data = view.buckets.map((bucket) => ({
    ...bucket,
    total: bucket.tokens.total,
    name: bucketLabel(
      bucket.start,
      view.filters.timezone!,
      view.filters.group!,
      true,
    ),
    fullLabel: bucketLabel(
      bucket.start,
      view.filters.timezone!,
      view.filters.group!,
    ),
  }));
  return (
    <div
      className="usage-chart"
      role="region"
      aria-label="Token usage over time"
    >
      <ResponsiveContainer width="100%" height={240} minWidth={0}>
        <BarChart
          data={data}
          accessibilityLayer
          margin={{ top: 8, right: 28, left: 0, bottom: 8 }}
        >
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="name"
            minTickGap={32}
            interval="preserveStartEnd"
            tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
          />
          <YAxis
            width={48}
            tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
            tickFormatter={(n: number) =>
              new Intl.NumberFormat(undefined, { notation: "compact" }).format(
                n,
              )
            }
          />
          <Tooltip
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="usage-chart-tooltip">
                  <p>{payload[0].payload.fullLabel}</p>
                  <p>{usageNumber(payload[0].payload.total)} tokens</p>
                </div>
              ) : null
            }
          />
          <Bar
            dataKey="total"
            name="Tokens"
            fill="var(--primary)"
            maxBarSize={40}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
