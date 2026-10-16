import { useState } from "react";
import type { components } from "./api-schema";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { useResource } from "./useResource";
import { ResourceRetry } from "./ResourceRetry";
import { ReviewerSettings } from "./ReviewerSettings";
import { ReviewControls, ReviewRounds } from "./TaskReview";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { questionHref } from "./Inbox";

type Status = components["schemas"]["MilestoneReviewStatus"];
type Review = components["schemas"]["MilestoneReview"];

export function MilestoneReviewSettings({
  workspaceId,
  milestoneId,
  refresh,
  onDirty,
}: {
  workspaceId: string;
  milestoneId: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
}) {
  const resource = useResource<Status>(
    `workspaces/${workspaceId}/milestones/${milestoneId}/automation`,
    refresh,
  );
  const [open, setOpen] = useState(false);
  const value = resource.data;
  return (
    <DetailSection title="Combined verification">
      <ContentStack>
        <ResourceRetry resources={[resource]} />
        {value && <p>{value.message}</p>}
        {value?.plan && (
          <Disclosure summary="Verification plan">
            <ContentStack>
              <p>
                Plan revision {value.plan.revision} · milestone revision{" "}
                {value.plan.milestone_revision}
              </p>
              <Markdown>{value.plan.instructions}</Markdown>
              <ol>
                {value.plan.checks.map((check, index) => (
                  <li key={index}>{check}</li>
                ))}
              </ol>
            </ContentStack>
          </Disclosure>
        )}
        <Disclosure
          summary="Milestone reviewer settings"
          onToggle={(event) => setOpen(event.currentTarget.open)}
        >
          <ReviewerSettings
            workspaceId={workspaceId}
            purpose="milestone"
            entity={milestoneId}
            refresh={refresh}
            onDirty={onDirty}
            active={open}
          />
        </Disclosure>
      </ContentStack>
    </DetailSection>
  );
}

export function MilestoneReviewRecord({
  projectId,
  reviewId,
  refresh,
}: {
  projectId: string;
  reviewId: string;
  refresh: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Disclosure
      summary="Review rounds and diagnostics"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      {open && (
        <ReviewRounds
          projectId={projectId}
          review={{ id: reviewId, revision: refresh }}
          kind="milestone-reviews"
        />
      )}
    </Disclosure>
  );
}

export function MilestoneReviewRecovery({
  projectId,
  reviewId,
  refresh,
}: {
  projectId: string;
  reviewId: string;
  refresh: unknown;
}) {
  const path = `projects/${projectId}/milestone-reviews/${reviewId}`;
  const resource = useResource<Review>(path, refresh);
  const review = resource.data;
  return (
    <ContentStack>
      <ResourceRetry resources={[resource]} />
      {review?.problem && <p>{review.problem}</p>}
      {review?.status === "waiting" && review.question_id && (
        <WorkspaceLink to={questionHref(review.project_id, review.question_id)}>
          Answer milestone review question
        </WorkspaceLink>
      )}
      {review && (
        <ReviewControls review={review} path={path} saved={resource.setData} />
      )}
    </ContentStack>
  );
}
