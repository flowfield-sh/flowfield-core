import { useCallback } from "react";
import { ContentStack } from "./DetailLayout";
import { MilestoneIntegrationSettings } from "./MilestoneIntegrationSettings";
import { ReviewerSettings } from "./ReviewerSettings";
import { DetailTabs } from "./Presentation";
import { useLocation, useSearchParams } from "react-router";
import { useDirtyItems } from "./useDirtyItems";
import type { DirtyCallback } from "./retainedState";
import type { Milestone } from "./workspace";
import type { components } from "./api-schema";

export function MilestoneSettings({
  milestone,
  delivery,
  refresh,
  onDirty,
}: {
  milestone: Milestone;
  delivery: components["schemas"]["MilestoneDelivery"] | null;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const mark = useDirtyItems(onDirty);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const tab = params.get("tab") === "reviewer" ? "reviewer" : "integration";
  const integrationDirty = useCallback(
    (value: boolean) => mark("integration", value),
    [mark],
  );
  const reviewerDirty = useCallback(
    (value: boolean) => mark("reviewer", value),
    [mark],
  );
  return (
    <ContentStack space="section" aria-label="Milestone settings">
      <DetailTabs
        id="milestone-settings"
        title="Milestone settings"
        tabs={["integration", "reviewer"]}
        active={tab}
        change={(value) => {
          const next = new URLSearchParams(params);
          next.set("tab", value);
          setParams(next, { state: location.state });
        }}
      />
      <div
        role="tabpanel"
        id="milestone-settings-integration"
        aria-labelledby="milestone-settings-integration-tab"
        hidden={tab !== "integration"}
      >
        {delivery?.status === "ready" ? (
          <MilestoneIntegrationSettings
            path={`workspaces/${milestone.workspace_id}/milestones/${milestone.id}`}
            refresh={refresh}
            onDirty={integrationDirty}
          />
        ) : (
          <p>{delivery?.problem ?? "Milestone delivery is not configured."}</p>
        )}
      </div>
      <div
        role="tabpanel"
        id="milestone-settings-reviewer"
        aria-labelledby="milestone-settings-reviewer-tab"
        hidden={tab !== "reviewer"}
      >
        <ReviewerSettings
          workspaceId={milestone.workspace_id}
          purpose="milestone"
          entity={milestone.id}
          refresh={refresh}
          onDirty={reviewerDirty}
        />
      </div>
    </ContentStack>
  );
}
