import { Button } from "@/components/ui/button";
import { DetailHeader } from "./Presentation";
import { MilestoneVerification } from "./MilestoneVerification";
import { Markdown } from "./Markdown";
import { ContentStack, DetailSection } from "./DetailLayout";
import { RevisionHistory } from "./RevisionHistory";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref } from "./navigation";
import type { Milestone } from "./workspace";

export function MilestoneDetail({
  milestone,
  refresh,
  onDirty,
  latest,
  historical,
  path,
  close,
}: {
  milestone: Milestone;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
  latest?: Milestone;
  historical: boolean;
  path: string;
  close: () => void;
}) {
  return (
    <section className="editor documents-page" aria-label="Milestone">
      <DetailHeader
        title={`${milestone.key} · ${milestone.title}`}
        close={close}
        closeLabel="Back"
        actions={
          <div className="actions justify-end">
            {historical && (
              <Button size="sm" variant="outline" asChild>
                <WorkspaceLink
                  to={`/workspaces/${milestone.workspace_id}/milestones/${milestone.key}`}
                >
                  Current milestone
                </WorkspaceLink>
              </Button>
            )}
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink
                to={boardHref(milestone.workspace_id, {
                  milestone: milestone.id,
                })}
              >
                View tasks
              </WorkspaceLink>
            </Button>
          </div>
        }
      />
      <div className="document-layout">
        <ContentStack space="section" className="document-content">
          <DetailSection title="Description">
            <Markdown>{milestone.body || "No description yet."}</Markdown>
          </DetailSection>
          <DetailSection title="Acceptance criteria">
            <Markdown>
              {milestone.acceptance_criteria || "No acceptance criteria yet."}
            </Markdown>
          </DetailSection>
          {!historical && (
            <MilestoneVerification
              key={milestone.id}
              refresh={refresh}
              onDirty={onDirty}
              path={path}
              milestoneId={milestone.id}
              milestoneKey={milestone.key}
              revision={milestone.revision}
            />
          )}
        </ContentStack>
        <RevisionHistory
          key={milestone.id}
          kind="Milestone"
          path={`${path}/milestones/${milestone.id}`}
          href={`/workspaces/${milestone.workspace_id}/milestones/${milestone.key}`}
          revision={latest?.revision ?? milestone.revision}
          selected={milestone.revision}
        />
      </div>
    </section>
  );
}
