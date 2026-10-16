import { WorkIdentity } from "./WorkIdentity";
import { MilestoneVerificationPlan } from "./MilestoneReviewSettings";
import { milestoneCards } from "./Milestones";
import { ResourceRetry } from "./ResourceRetry";
import type { WorkspaceBoard } from "./workspace";
import type { DirtyCallback } from "./retainedState";
import { Button } from "@/components/ui/button";
import { useLocation, useParams, useSearchParams } from "react-router";
import { DetailTabs } from "./Presentation";
import { DetailHeader } from "./Presentation";
import { MilestoneActivity } from "./MilestoneActivity";
import { Markdown } from "./Markdown";
import { ContentStack, DetailSection } from "./DetailLayout";
import { MilestoneSettings } from "./MilestoneSettings";
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
  const settings = useParams().milestoneTab === "settings";
  const href = `/workspaces/${milestone.workspace_id}/milestones/${milestone.key}`;
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
          <WorkIdentity
            state={historical ? undefined : card?.workState}
            itemKey={milestone.key}
            title={settings ? "Milestone settings" : milestone.title}
          />
        }
        close={close}
        closeLabel="Back"
        actions={
          <div className="actions">
            {!historical && !settings && (
              <Button size="sm" variant="outline" asChild>
                <WorkspaceLink to={`${href}/settings`}>Settings</WorkspaceLink>
              </Button>
            )}
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
      {!historical && !settings && (
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
      {settings ? (
        <>
          <ResourceRetry resources={[delivery]} />
          {delivery.loading && !delivery.data ? (
            <p>Loading settings…</p>
          ) : (
            !delivery.error && (
              <MilestoneSettings
                milestone={milestone}
                delivery={delivery.data}
                refresh={refresh}
                onDirty={onDirty}
              />
            )
          )}
        </>
      ) : tab === "overview" ? (
        <ContentStack space="section">
          <Markdown>{milestone.body || "No description yet."}</Markdown>
          <ResourceRetry resources={[delivery]} />
          {delivery.data && (
            <DetailSection title="Delivery">
              <p>
                Project:{" "}
                <WorkspaceLink to={`/projects/${delivery.data.project_id}`}>
                  {board.projects.find(
                    (project) => project.id === delivery.data?.project_id,
                  )?.name ?? delivery.data.project_id}
                </WorkspaceLink>
              </p>
              <p className="delivery-branches">
                <span>Merge:</span>
                <code>{delivery.data.branch}</code>
                <span aria-label="to">→</span>
                <code>{delivery.data.destination_branch}</code>
              </p>
            </DetailSection>
          )}
          <DetailSection title="Acceptance criteria">
            <Markdown>
              {milestone.acceptance_criteria || "No acceptance criteria yet."}
            </Markdown>
          </DetailSection>
          <MilestoneVerificationPlan
            workspaceId={milestone.workspace_id}
            milestoneId={milestone.id}
            refresh={refresh}
          />
          <ResourceRetry resources={[latestReview]} />
          {latestReview.data?.summary && (
            <DetailSection title="Outcome">
              <Markdown>{latestReview.data.summary}</Markdown>
            </DetailSection>
          )}
          <DetailSection title="Contributing work">
            {!!card?.tasks.length && (
              <ul className="work-resource-list">
                {card?.tasks.map((task) => (
                  <li key={task.key}>
                    <WorkspaceLink
                      className="work-navigation-row"
                      to={taskHref(task.project_id, task)}
                    >
                      <WorkIdentity
                        state={task.state}
                        itemKey={task.key}
                        title={task.title}
                      />
                    </WorkspaceLink>
                  </li>
                ))}
              </ul>
            )}
            {!card?.tasks.length && <p>No contributing tasks yet.</p>}
          </DetailSection>
        </ContentStack>
      ) : tab === "activity" ? (
        <MilestoneActivity
          key={milestone.id}
          refresh={refresh}
          onDirty={onDirty}
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
