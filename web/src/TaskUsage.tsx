import { useState } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import {
  UsageBreakdowns,
  UsageCoverageDetails,
  UsageExecutionList,
  UsageReadState,
} from "./UsageDetails";
import {
  usageNumber,
  usageDuration,
  usageCoverage,
  useUsagePage,
  type UsageView,
} from "./usage";

/** Lifetime task totals come only from the charged-task read, never related activity. */
export function TaskUsage({
  workspaceId,
  taskId,
  refresh,
}: {
  workspaceId: string;
  taskId: string;
  refresh: unknown;
}) {
  const resource = useUsagePage<UsageView>(
    `workspaces/${encodeURIComponent(workspaceId)}/tasks/${encodeURIComponent(taskId)}/usage`,
    refresh,
  );
  const view = resource.data;
  const [detailsLoaded, setDetailsLoaded] = useState(false);
  return (
    <DetailSection title="Usage" aria-label="Task usage" className="task-usage">
      <UsageReadState resource={resource} name="task usage" />
      {view && (
        <>
          {view.totals.attempts === 0 ? (
            <p>No recorded executions yet.</p>
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
                {view.totals.unknown_intervals > 0 && " · Some timing unknown"}
              </p>
            </ContentStack>
          )}
          <Disclosure
            summary="Usage details"
            onToggle={(event) => {
              if (event.currentTarget.open) setDetailsLoaded(true);
            }}
          >
            {detailsLoaded && (
              <>
                <p>{view.totals.attempts} execution attempts</p>
                <Disclosure summary="Accounting and coverage">
                  <UsageCoverageDetails view={view} />
                  <p>
                    Includes bound subtasks and review clarifications. Result
                    references and assembly add no usage.
                  </p>
                  {view.related_coordinator_attempts > 0 && (
                    <p>
                      {view.related_coordinator_attempts} related Coordinator
                      attempts are excluded from task totals.
                    </p>
                  )}
                </Disclosure>
                <UsageBreakdowns view={view} />
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
        </>
      )}
    </DetailSection>
  );
}
