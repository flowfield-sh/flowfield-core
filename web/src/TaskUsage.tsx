import { DetailSection } from "./DetailLayout";
import { UsageMetrics, UsageReadState } from "./UsageMetrics";
import { useUsagePage } from "./usage";

export function TaskUsage({
  workspaceId,
  taskId,
  refresh,
}: {
  workspaceId: string;
  taskId: string;
  refresh: unknown;
}) {
  const resource = useUsagePage(
    `workspaces/${encodeURIComponent(workspaceId)}/tasks/${encodeURIComponent(taskId)}/usage`,
    refresh,
  );
  return (
    <DetailSection
      title="Metrics"
      aria-label="Task metrics"
      className="task-usage"
    >
      <UsageReadState resource={resource} name="task metrics" />
      {resource.data &&
        (resource.data.totals.attempts ? (
          <UsageMetrics view={resource.data} task />
        ) : (
          <p className="detail-metadata">No metrics yet.</p>
        ))}
    </DetailSection>
  );
}
