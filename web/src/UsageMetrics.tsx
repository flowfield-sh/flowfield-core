import { useRef, useState, type ReactNode } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { usageCost, usageNumber, useUsageClock, type UsageView } from "./usage";
import { Card } from "@/components/ui/card";
import { ResourceRetry } from "./ResourceRetry";

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

export function UsageMetrics({
  view,
  task = false,
}: {
  view: UsageView;
  task?: boolean;
}) {
  const duration = useUsageClock(view);
  const roles = task
    ? ["worker", "reviewer"]
    : ["coordinator", "worker", "reviewer"];
  const live = view.breakdowns.some((r) => r.dimension === "role" && r.live);
  function breakdown(metric: "tokens" | "duration" | "cost") {
    return (
      <dl className="usage-tooltip-rows">
        {roles.map((role) => {
          const row = view.breakdowns.find(
            (r) => r.dimension === "role" && r.key === role,
          );
          if (!row) return null;
          return (
            <div key={role}>
              <dt>{role[0].toUpperCase() + role.slice(1)}</dt>
              <dd>
                {metric === "tokens"
                  ? usageNumber(row.tokens.total)
                  : metric === "cost"
                    ? usageCost(row.cost.nanodollars, true)
                    : duration(row.elapsed_seconds, row.live)}
              </dd>
            </div>
          );
        })}
      </dl>
    );
  }
  return (
    <div className="usage-metrics">
      <dl
        className={`usage-totals metrics-cards${task ? " metrics-cards-task" : ""}`}
      >
        <Card className="gap-1 rounded-lg p-3 shadow-none">
          <dt>Tokens</dt>
          <dd>
            <MetricValue
              label="Token usage breakdown"
              value={usageNumber(view.totals.tokens.total)}
            >
              {breakdown("tokens")}
            </MetricValue>
          </dd>
        </Card>
        <Card className="gap-1 rounded-lg p-3 shadow-none">
          <dt>Duration</dt>
          <dd>
            <MetricValue
              label="Duration breakdown"
              value={duration(view.totals.elapsed_seconds)}
            >
              {breakdown("duration")}
            </MetricValue>
          </dd>
        </Card>
        <Card className="gap-1 rounded-lg p-3 shadow-none">
          <dt>Cost</dt>
          <dd>
            <MetricValue
              label="Cost breakdown"
              value={usageCost(view.totals.cost.nanodollars)}
            >
              {breakdown("cost")}
              {view.totals.cost.rate_dates.length > 0 && (
                <p>
                  Based on API costs as of{" "}
                  {view.totals.cost.rate_dates.join(", ")}.
                </p>
              )}
              {view.totals.cost.coverage !== "complete" && (
                <p>Some usage or pricing data is unavailable.</p>
              )}
            </MetricValue>
          </dd>
        </Card>
        {task &&
          (
            [
              [
                "Corrections",
                view.totals.corrections,
                "Work started to address review or human feedback.",
              ],
              [
                "Retries",
                view.totals.retries,
                "Worker attempts started again after failure or stopping.",
              ],
            ] as const
          ).map(([label, value, explanation]) => (
            <Card key={label} className="gap-1 rounded-lg p-3 shadow-none">
              <dt>{label}</dt>
              <dd>
                <MetricValue
                  label={label + " explained"}
                  value={usageNumber(value)}
                >
                  <p>{explanation} Answer continuations are excluded.</p>
                </MetricValue>
              </dd>
            </Card>
          ))}
        {live && <span className="usage-live">Running</span>}
      </dl>
      {view.limit_reached && (
        <p role="status">
          This selection is too large. Choose a shorter date range to see all
          usage.
        </p>
      )}
    </div>
  );
}

function MetricValue({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const openOnPointerDown = useRef(false);
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="usage-metric-trigger"
          aria-label={`${label}: ${value}`}
          onPointerDown={() => {
            openOnPointerDown.current = open;
          }}
          onClick={(event) => {
            event.preventDefault();
            // Touch can focus (and open) the tooltip before click arrives.
            // Toggle from pointer-down state rather than that intermediate focus.
            setOpen(event.detail === 0 || !openOnPointerDown.current);
          }}
        >
          {value}
        </button>
      </TooltipTrigger>
      <TooltipContent>{children}</TooltipContent>
    </Tooltip>
  );
}
