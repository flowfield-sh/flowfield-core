import { useCallback } from "react";
import { ContentStack, DetailSection } from "./DetailLayout";
import { MilestoneIntegrationSettings } from "./MilestoneIntegrationSettings";
import { ReviewerSettings } from "./ReviewerSettings";
import { Separator } from "@/components/ui/separator";
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
      <DetailSection title="Integration">
        {delivery?.status === "ready" ? (
          <MilestoneIntegrationSettings
            path={`workspaces/${milestone.workspace_id}/milestones/${milestone.id}`}
            refresh={refresh}
            onDirty={integrationDirty}
          />
        ) : (
          <p>{delivery?.problem ?? "Milestone delivery is not configured."}</p>
        )}
      </DetailSection>
      <Separator />
      <DetailSection title="Reviewer">
        <ReviewerSettings
          workspaceId={milestone.workspace_id}
          purpose="milestone"
          entity={milestone.id}
          refresh={refresh}
          onDirty={reviewerDirty}
        />
      </DetailSection>
    </ContentStack>
  );
}
