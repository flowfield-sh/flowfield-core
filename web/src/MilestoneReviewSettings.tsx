import { useState } from "react";
import type { components } from "./api-schema";
import { ContentStack, Disclosure } from "./DetailLayout";
import { useResource } from "./useResource";
import { ResourceRetry } from "./ResourceRetry";
import { ReviewControls, ReviewRounds } from "./TaskReview";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { questionHref } from "./Inbox";

type Status = components["schemas"]["MilestoneReviewStatus"];
type Review = components["schemas"]["MilestoneReview"];

export function MilestoneVerificationPlan({
  workspaceId,
  milestoneId,
  refresh,
}: {
  workspaceId: string;
  milestoneId: string;
  refresh: unknown;
}) {
  const resource = useResource<Status>(
    `workspaces/${workspaceId}/milestones/${milestoneId}/automation`,
    refresh,
  );
  const plan = resource.data?.plan;
  return (
    <>
      <ResourceRetry resources={[resource]} />
      {plan && (
        <Disclosure summary="Verification plan">
          <ContentStack>
            <p className="detail-metadata">
              Plan revision {plan.revision} · milestone revision{" "}
              {plan.milestone_revision}
            </p>
            <Disclosure summary="Instructions">
              <Markdown>{plan.instructions}</Markdown>
            </Disclosure>
            <Disclosure summary="Checks">
              <ol>
                {plan.checks.map((check, index) => (
                  <li key={index}>{check}</li>
                ))}
              </ol>
            </Disclosure>
          </ContentStack>
        </Disclosure>
      )}
    </>
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
