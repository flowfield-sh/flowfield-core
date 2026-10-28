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
    <DetailSection title="Usage" aria-label="Task usage" className="task-usage">
      <UsageReadState resource={resource} name="task usage" />
      {resource.data &&
        (resource.data.totals.attempts ? (
          <UsageMetrics view={resource.data} task />
        ) : (
          <p className="detail-metadata">No usage yet.</p>
        ))}
    </DetailSection>
  );
}
