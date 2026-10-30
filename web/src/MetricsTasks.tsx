import { Badge } from "@/components/ui/badge";
import { useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { LoadMore } from "./LoadMore";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import {
  costAmount,
  usageCost,
  usageNumber,
  useUsageClock,
  type UsageView,
} from "./usage";

export function MetricsTasks({
  workspaceId,
  view,
  sort,
  change,
}: {
  workspaceId: string;
  view: UsageView;
  sort: string;
  change: (sort: string) => void;
}) {
  const [limit, setLimit] = useState(50);
  const duration = useUsageClock(view);
  const field = sort.startsWith("duration")
    ? "duration"
    : sort.startsWith("cost")
      ? "cost"
      : "tokens";
  const ascending = sort.endsWith("-asc");
  const rows = [...view.tasks].sort((a, b) => {
    const left =
      field === "tokens"
        ? a.tokens.total
        : field === "cost"
          ? costAmount(a.cost)
          : a.elapsed_seconds;
    const right =
      field === "tokens"
        ? b.tokens.total
        : field === "cost"
          ? costAmount(b.cost)
          : b.elapsed_seconds;
    if (left == null && right != null) return 1;
    if (right == null && left != null) return -1;
    return (
      ((left ?? 0) - (right ?? 0)) * (ascending ? 1 : -1) ||
      a.key.localeCompare(b.key, undefined, { numeric: true })
    );
  });
  if (!rows.length) return <p>No task activity in this period.</p>;
  return (
    <>
      <div className="metrics-table">
        <table
          className="metrics-tasks"
          aria-label="Task metrics for selected period"
        >
          <thead>
            <tr>
              <th scope="col">Task</th>
              {(["tokens", "cost", "duration"] as const).map((key) => (
                <th
                  key={key}
                  scope="col"
                  aria-sort={
                    field === key
                      ? ascending
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  <button
                    type="button"
                    onClick={() =>
                      change(
                        `${key}-${field === key && !ascending ? "asc" : "desc"}`,
                      )
                    }
                  >
                    {key === "tokens"
                      ? "Tokens"
                      : key === "cost"
                        ? "Cost"
                        : "Duration"}
                    {field === key &&
                      (ascending ? (
                        <ArrowUp size={12} aria-hidden="true" />
                      ) : (
                        <ArrowDown size={12} aria-hidden="true" />
                      ))}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((row) => (
              <tr key={row.task_id}>
                <td>
                  <WorkspaceLink to={taskHref(workspaceId, row)}>
                    <Badge variant="outline">{row.key}</Badge> {row.title}
                  </WorkspaceLink>
                </td>
                <td>{usageNumber(row.tokens.total)}</td>
                <td>{usageCost(costAmount(row.cost))}</td>
                <td>{duration(row.elapsed_seconds, row.live)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <LoadMore onClick={() => setLimit((n) => n + 50)}>
          Load more tasks
        </LoadMore>
      )}
    </>
  );
}
