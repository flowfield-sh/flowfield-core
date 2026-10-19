import { LoadMore } from "./LoadMore";
import { useContext, useState } from "react";
import { ActivityBodyVisible } from "./ActivityEntry";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import type { components } from "./api-schema";
import { usePage, useResource } from "./useResource";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "./ConfirmButton";
import { RunActivity } from "./RunActivity";
import { Timestamp } from "./Timestamp";
import { ResourceRetry } from "./ResourceRetry";

export function ReviewActivity({
  projectId,
  reviewId,
  refresh,
}: {
  projectId: string;
  reviewId: string;
  refresh: unknown;
}) {
  const visible = useContext(ActivityBodyVisible);
  const resource = useResource<Review>(
    visible ? `workspaces/${projectId}/reviews/${reviewId}` : null,
    refresh,
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[resource]} />
      {resource.data && (
        <>
          {resource.data.problem && <p>{resource.data.problem}</p>}
          <ReviewRounds projectId={projectId} review={resource.data} />
        </>
      )}
    </ContentStack>
  );
}

type Review = components["schemas"]["TaskReview"];
export function TaskReview({
  projectId,
  taskId,
  taskKey,
  refresh,
}: {
  projectId: string;
  taskId: string;
  taskKey: string;
  refresh: unknown;
}) {
  const resource = useResource<Review | null>(
    `workspaces/${projectId}/tasks/${taskId}/review`,
    refresh,
  );
  const review = resource.data;
  if (review && ["decided", "stale"].includes(review.status)) return null;
  if (!review && !resource.error) return null;
  return (
    <DetailSection title="Independent review">
      <ContentStack>
        <ResourceRetry resources={[resource]}>Retry review</ResourceRetry>
        {review && (
          <>
            <p>
              {review.status === "decided"
                ? review.verdict?.summary
                : review.status === "running"
                  ? "Reviewing result"
                  : review.status === "queued"
                    ? "Review queued"
                    : review.status === "waiting"
                      ? "Waiting for your answer"
                      : review.status === "uncertain"
                        ? "Check reviewer state"
                        : review.status === "stopped"
                          ? "Review stopped"
                          : review.status === "stale"
                            ? "Review is outdated"
                            : review.status === "failed"
                              ? "Review failed"
                              : "Preparing review"}
            </p>
            {review.problem && <p>{review.problem}</p>}
            <ReviewControls
              review={review}
              path={`workspaces/${projectId}/reviews/${review.id}`}
              saved={resource.setData}
            />
            {["uncertain", "failed", "stopped"].includes(review.status) && (
              <WorkspaceLink
                to={`${taskHref(projectId, { key: taskKey })}/conversation/review:${review.id}`}
              >
                Review rounds and diagnostics
              </WorkspaceLink>
            )}
          </>
        )}
      </ContentStack>
    </DetailSection>
  );
}

function ReviewRounds({
  projectId,
  review,
}: {
  projectId: string;
  review: { id: string; revision: number };
}) {
  const page = usePage<components["schemas"]["ReviewAttemptPage"]>(
    `workspaces/${projectId}/reviews/${review.id}/attempts`,
    review.revision,
    true,
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[page]}>Retry review history</ResourceRetry>
      {page.data?.items.map((attempt, index) => (
        <ReviewRound
          key={attempt.id}
          projectId={projectId}
          reviewId={review.id}
          attempt={attempt}
          inline={index === 0}
        />
      ))}
      {page.data?.next_before != null && (
        <LoadMore
          size="sm"
          variant="outline"
          disabled={page.loadingMore || page.loading}
          onClick={() => void page.older()}
        >
          Earlier rounds
        </LoadMore>
      )}
    </ContentStack>
  );
}

function ReviewRound({
  projectId,
  reviewId,
  attempt,
  inline,
}: {
  projectId: string;
  reviewId: string;
  attempt: components["schemas"]["ReviewAttemptInfo"];
  inline: boolean;
}) {
  const [manual, setManual] = useState<boolean | null>(null);
  const open =
    manual ??
    ["preparing", "running", "waiting", "uncertain"].includes(attempt.status);
  const body = (
    <ContentStack>
      {attempt.problem && <p>{attempt.problem}</p>}
      <RunActivity
        projectId={projectId}
        runId={reviewId}
        review
        reviewAttemptId={attempt.id}
      />
      {attempt.status === "uncertain" && !attempt.cleanup_confirmed && (
        <Disclosure summary="Process evidence">
          <p>
            Attempt: <code>{attempt.id}</code>
          </p>
          <p>
            Requested model: {attempt.requested_agent?.model || "Not launched"}
          </p>
          <p>
            Applied model: {attempt.applied_agent?.model || "Not confirmed"}
          </p>
          <p>PID: {attempt.pid ?? "Not recorded"}</p>
          <p>
            Process identity:{" "}
            <code>{attempt.process_stamp || "Not recorded"}</code>
          </p>
          <p>
            Checkout: <code>{attempt.checkout || "Not prepared"}</code>
          </p>
        </Disclosure>
      )}
    </ContentStack>
  );
  if (inline) return body;
  return (
    <Disclosure
      open={open}
      summary={
        <>
          {attempt.role === "reviewer" ? "Reviewer" : "Implementer"} ·{" "}
          <Timestamp date={attempt.created_at} />
        </>
      }
      onClick={(event) => {
        if (
          (event.target as Element).closest("summary")?.parentElement ===
          event.currentTarget
        ) {
          event.preventDefault();
          setManual(!open);
        }
      }}
    >
      {open && body}
    </Disclosure>
  );
}

function ReviewControls<R extends { revision: number; status: string }>({
  review,
  path,
  saved: onSaved,
}: {
  review: R;
  path: string;
  saved: (value: R) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function act(action: "retry" | "confirm-stopped") {
    if (!review) return;
    setBusy(true);
    try {
      const saved = await request<R>(`${path}/${action}`, "POST", {
        expected_revision: review.revision,
      });
      onSaved(saved);
    } catch (error) {
      reportError(error, "Could not update review");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="actions">
      {review.status === "uncertain" && (
        <ConfirmButton
          size="sm"
          disabled={busy}
          title="Confirm all review processes stopped?"
          description="Inspect the retained process evidence first. Confirm only after the reviewer and its commands have stopped. This releases its reserved capacity without applying its conclusion."
          action={() => void act("confirm-stopped")}
        >
          Confirm stopped
        </ConfirmButton>
      )}
      {["failed", "stopped"].includes(review.status) && (
        <Button size="sm" disabled={busy} onClick={() => void act("retry")}>
          Retry review
        </Button>
      )}
    </div>
  );
}
