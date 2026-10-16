import { WorkState } from "./WorkState";
import { SourceReferences } from "./SourceReferences";
import { milestoneCards } from "./Milestones";
import { ResourceRetry } from "./ResourceRetry";
import type { WorkspaceBoard } from "./workspace";
import type { DirtyCallback } from "./retainedState";
import { Button } from "@/components/ui/button";
import { useLocation, useSearchParams } from "react-router";
import { DetailTabs } from "./Presentation";
import { DetailHeader } from "./Presentation";
import { MilestoneActivity } from "./MilestoneActivity";
import { Markdown } from "./Markdown";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { MilestoneIntegrationSettings } from "./MilestoneIntegrationSettings";
import { MilestoneReviewSettings } from "./MilestoneReviewSettings";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
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
  const [integrationDirty, setIntegrationDirty] = useState(false);
  const [reviewerDirty, setReviewerDirty] = useState(false);
  const [responseDirty, setResponseDirty] = useState(false);
  const [responseRetained, setResponseRetained] = useState(false);
  const markResponseDirty = useCallback<DirtyCallback>(
    (dirty, retained = false) => {
      setResponseDirty(dirty);
      setResponseRetained(retained);
    },
    [],
  );
  useEffect(() => {
    onDirty(
      integrationDirty || reviewerDirty || responseDirty,
      responseRetained && !integrationDirty && !reviewerDirty,
    );
    return () => onDirty(false);
  }, [
    onDirty,
    integrationDirty,
    reviewerDirty,
    responseDirty,
    responseRetained,
  ]);
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
  const tab = historical
    ? "revisions"
    : params.has("verification") ||
        ["activity", "review", "history", "details"].includes(
          params.get("tab") ?? "",
        )
      ? "activity"
      : "overview";
  return (
    <section className="editor documents-page" aria-label="Milestone">
      <DetailHeader
        title={
          <span className="entity-heading-identity">
            {!historical && <WorkState indicatorOnly state={card?.workState} />}
            <Badge variant="outline">{milestone.key}</Badge>
            <span>{milestone.title}</span>
          </span>
        }
        close={close}
        closeLabel="Back"
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
          tabs={["overview", "activity"]}
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
          <ResourceRetry resources={[delivery]} />
          {delivery.data && (
            <DetailSection title="Delivery destination">
              <p>
                Final approval delivers the verified result to{" "}
                <strong>{delivery.data.destination_branch}</strong> in{" "}
                <WorkspaceLink to={`/projects/${delivery.data.project_id}`}>
                  {board.projects.find(
                    (project) => project.id === delivery.data?.project_id,
                  )?.name ?? delivery.data.project_id}
                </WorkspaceLink>
                .
              </p>
              <p>
                Task results are assembled and checked on the milestone’s
                separate branch before your final approval.
              </p>
            </DetailSection>
          )}
          {card?.verification && (
            <WorkspaceLink
              to={`/workspaces/${milestone.workspace_id}/milestones/${milestone.key}?tab=activity&verification=${card.verification.id}`}
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
          {latestReview.data?.summary && (
            <DetailSection title="Outcome">
              <Markdown>{latestReview.data.summary}</Markdown>
            </DetailSection>
          )}
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
          {delivery.data?.status === "ready" && (
            <Disclosure summary="Task integration">
              <MilestoneIntegrationSettings
                path={`${path}/milestones/${milestone.id}`}
                refresh={refresh}
                onDirty={setIntegrationDirty}
              />
            </Disclosure>
          )}
          {delivery.data && (
            <MilestoneReviewSettings
              workspaceId={milestone.workspace_id}
              milestoneId={milestone.id}
              refresh={refresh}
              onDirty={setReviewerDirty}
            />
          )}
        </ContentStack>
      ) : tab === "activity" ? (
        <MilestoneActivity
          key={milestone.id}
          refresh={refresh}
          onDirty={markResponseDirty}
          path={path}
          milestone={milestone}
          newest={card?.verification?.id}
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
