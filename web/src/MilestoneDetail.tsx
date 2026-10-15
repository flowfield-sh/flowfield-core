import { WorkState } from "./WorkState";
import { SourceReferences } from "./SourceReferences";
import { milestoneCards, milestoneState } from "./Milestones";
import { MilestoneReport } from "./MilestoneVerification";
import { ResourceRetry } from "./ResourceRetry";
import type { WorkspaceBoard } from "./workspace";
import type { DirtyCallback } from "./retainedState";
import { Button } from "@/components/ui/button";
import { useLocation, useSearchParams } from "react-router";
import { DetailTabs } from "./Presentation";
import { DetailHeader } from "./Presentation";
import { MilestoneVerification } from "./MilestoneVerification";
import { Markdown } from "./Markdown";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { RevisionHistory } from "./RevisionHistory";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref, taskHref } from "./navigation";
import type { Milestone } from "./workspace";
import type { components } from "./api-schema";
import { useResource } from "./useResource";

export function MilestoneDetail({
  milestone,
  board,
  refresh,
  onDirty,
  latest,
  historical,
  path,
  close,
}: {
  milestone: Milestone;
  board: WorkspaceBoard;
  refresh: unknown;
  onDirty: DirtyCallback;
  latest?: Milestone;
  historical: boolean;
  path: string;
  close: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const delivery = useResource<
    components["schemas"]["MilestoneDelivery"] | null
  >(`${path}/milestones/${milestone.id}/delivery`, refresh);
  const card = milestoneCards(board).find(
    (card) => card.milestone.id === milestone.id,
  );
  const latestReview = useResource<
    components["schemas"]["MilestoneVerification"]
  >(
    card?.verification ? `${path}/verifications/${card.verification.id}` : null,
    refresh,
  );
  const tab =
    !historical &&
    (params.get("tab") === "review" || params.has("verification"))
      ? "review"
      : historical || params.get("tab") === "details"
        ? "details"
        : "overview";
  return (
    <section className="editor documents-page" aria-label="Milestone">
      <DetailHeader
        title={`${milestone.key} · ${milestone.title}`}
        close={close}
        closeLabel="Back"
        metadata={
          delivery.data &&
          `${delivery.data.project_id} → ${delivery.data.destination_branch}`
        }
        actions={
          <div className="actions">
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
      {!historical && (
        <DetailTabs
          id="milestone"
          title="Milestone views"
          tabs={["overview", "details", "review"]}
          active={tab}
          change={(value) => {
            const next = new URLSearchParams(params);
            next.delete("verification");
            next.set("tab", value);
            setParams(next, { state: location.state });
          }}
        />
      )}
      {tab === "overview" ? (
        <ContentStack space="section">
          <Markdown>{milestone.body || "No description yet."}</Markdown>
          <WorkState
            state={
              milestoneState(
                card?.verification,
                !!card?.tasks.length && card.done === card.tasks.length,
              ) ?? {
                label: card?.column ?? "Planned",
                tone: card?.column === "Complete" ? "complete" : "idle",
              }
            }
          />
          {card?.verification && (
            <WorkspaceLink
              to={`/workspaces/${milestone.workspace_id}/milestones/${milestone.key}?tab=review&verification=${card.verification.id}`}
            >
              Inspect review
              {card.verification.status === "ready" ||
              card.verification.status === "delivery_blocked"
                ? " and respond"
                : ""}
            </WorkspaceLink>
          )}
          <DetailSection title="Acceptance criteria">
            <Markdown>
              {milestone.acceptance_criteria || "No acceptance criteria yet."}
            </Markdown>
          </DetailSection>
          <ResourceRetry resources={[latestReview]} />
          {latestReview.data && <MilestoneReport value={latestReview.data} />}
          <DetailSection title="Contributing work">
            <ul className="source-reference-list">
              {card?.tasks.map((task) => (
                <li key={task.key}>
                  <WorkspaceLink to={taskHref(task.project_id, task)}>
                    {task.key} {task.title}
                  </WorkspaceLink>{" "}
                  <WorkState state={task.state} />
                </li>
              ))}
            </ul>
            {!card?.tasks.length && <p>No contributing tasks yet.</p>}
          </DetailSection>
          <SourceReferences
            title="Documents captured by tasks"
            updates={[
              ...new Map(
                board.tasks
                  .filter((task) => task.milestone_id === milestone.id)
                  .flatMap((task) => task.document_updates ?? [])
                  .map((update) => [update.id, update]),
              ).values(),
            ]}
            documents={board.tasks
              .filter((task) => task.milestone_id === milestone.id)
              .flatMap((task) => task.publication?.documents ?? [])}
          />
          <Disclosure summary="Work and review details">
            <WorkspaceLink to={`?tab=details`}>
              Description and revision history
            </WorkspaceLink>
            <WorkspaceLink to={`?tab=review`}>Review history</WorkspaceLink>
          </Disclosure>
        </ContentStack>
      ) : tab === "review" ? (
        <MilestoneVerification
          key={milestone.id}
          refresh={refresh}
          onDirty={onDirty}
          path={path}
          milestoneId={milestone.id}
          milestoneKey={milestone.key}
          revision={milestone.revision}
        />
      ) : (
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
      )}
    </section>
  );
}
