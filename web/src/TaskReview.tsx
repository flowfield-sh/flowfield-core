import { LoadMore } from "./LoadMore";
import { useState } from "react";
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

type Review = components["schemas"]["TaskReview"];
export function TaskReview({
  projectId,
  taskId,
  refresh,
}: {
  projectId: string;
  taskId: string;
  refresh: unknown;
}) {
  const resource = useResource<Review | null>(
    `projects/${projectId}/tasks/${taskId}/review`,
    refresh,
  );
  const [historyOpen, setHistoryOpen] = useState(false);
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
                  ? "Reviewing changes"
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
              path={`projects/${projectId}/reviews/${review.id}`}
              saved={resource.setData}
            />
            {["uncertain", "failed", "stopped"].includes(review.status) && (
              <Disclosure
                summary="Review rounds and diagnostics"
                onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
              >
                {historyOpen && (
                  <ReviewRounds
                    key={review.id}
                    projectId={projectId}
                    review={review}
                  />
                )}
              </Disclosure>
            )}
          </>
        )}
      </ContentStack>
    </DetailSection>
  );
}

export function ReviewRounds({
  projectId,
  review,
  kind = "reviews",
}: {
  projectId: string;
  review: { id: string; revision: number };
  kind?: "reviews" | "milestone-reviews";
}) {
  const page = usePage<components["schemas"]["ReviewAttemptPage"]>(
    `projects/${projectId}/${kind}/${review.id}/attempts`,
    review.revision,
    true,
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[page]}>Retry review history</ResourceRetry>
      {page.data?.items.map((attempt) => (
        <ReviewRound
          key={attempt.id}
          projectId={projectId}
          kind={kind}
          reviewId={review.id}
          attempt={attempt}
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
  kind,
  reviewId,
  attempt,
}: {
  projectId: string;
  reviewId: string;
  kind: "reviews" | "milestone-reviews";
  attempt: components["schemas"]["ReviewAttemptInfo"];
}) {
  const [open, setOpen] = useState(false);
  return (
    <Disclosure
      summary={
        <>
          {attempt.role === "reviewer" ? "Reviewer" : "Implementer"} ·{" "}
          <Timestamp date={attempt.created_at} />
        </>
      }
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      {open && (
        <ContentStack>
          {attempt.problem && <p>{attempt.problem}</p>}
          <RunActivity
            projectId={projectId}
            runId={reviewId}
            review
            reviewKind={kind}
            reviewAttemptId={attempt.id}
          />
          {attempt.status === "uncertain" && !attempt.cleanup_confirmed && (
            <Disclosure summary="Process evidence">
              <p>
                Attempt: <code>{attempt.id}</code>
              </p>
              <p>
                Requested model:{" "}
                {attempt.requested_agent?.model || "Not launched"}
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
      )}
    </Disclosure>
  );
}

export function ReviewControls<R extends { revision: number; status: string }>({
  review,
  path,
  saved: onSaved,
}: {
  review: R;
  path: string;
  saved: (value: R) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function act(action: "stop" | "retry" | "confirm-stopped") {
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
      {["queued", "preparing", "running", "waiting", "uncertain"].includes(
        review.status,
      ) && (
        <ConfirmButton
          size="sm"
          variant="outline"
          disabled={busy}
          title="Stop this review?"
          description="Retained findings and the candidate are preserved. The queue remains unchanged."
          action={() => void act("stop")}
        >
          Stop review
        </ConfirmButton>
      )}
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
