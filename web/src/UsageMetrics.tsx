import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { usageNumber, useUsageClock, type UsageView } from "./usage";
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
  return (
    <div className="usage-metrics">
      <dl className="usage-totals">
        <div>
          <dt>Total tokens</dt>
          <dd>{usageNumber(view.totals.tokens.total)}</dd>
        </div>
        <div>
          <dt>
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0}>Duration</span>
              </TooltipTrigger>
              <TooltipContent>
                Time with work running, including tools. Parallel work counts
                once; idle time between runs is excluded.
              </TooltipContent>
            </Tooltip>
          </dt>
          <dd>{duration(view.totals.elapsed_seconds)}</dd>
        </div>
        {live && <span className="usage-live">Running</span>}
      </dl>
      <div className="usage-roles">
        {roles.map((role) => {
          const row = view.breakdowns.find(
            (r) => r.dimension === "role" && r.key === role,
          );
          if (!row) return null;
          return (
            <div className="usage-role" key={role} aria-label={`${role} usage`}>
              <span>{role[0].toUpperCase() + role.slice(1)}</span>
              <span>
                {usageNumber(row.tokens.total)}{" "}
                <span className="detail-metadata">tokens</span>
              </span>
              <span className="detail-metadata">
                {duration(row.elapsed_seconds, row.live)}
              </span>
            </div>
          );
        })}
      </div>
      {view.limit_reached && (
        <p role="status">
          This selection is too large. Choose a shorter date range to see all
          usage.
        </p>
      )}
    </div>
  );
}
