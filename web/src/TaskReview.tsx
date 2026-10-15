import { useState } from "react";
import type { components } from "./api-schema";
import { usePage, useResource } from "./useResource";
import { request } from "./workspace";
import { reportError } from "./requestFeedback";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { Button } from "@/components/ui/button";
import { ReviewerSettings } from "./ReviewerSettings";
import { ConfirmButton } from "./ConfirmButton";
import { RunActivity } from "./RunActivity";
import { WorkspaceLink } from "./WorkspaceLink";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Timestamp } from "./Timestamp";
import { ResourceRetry } from "./ResourceRetry";

type Review = components["schemas"]["TaskReview"];
export function TaskReview({
  projectId,
  taskId,
  workspaceId,
  refresh,
  onDirty,
}: {
  projectId: string;
  taskId: string;
  workspaceId: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
}) {
  const resource = useResource<Review | null>(
    `projects/${projectId}/tasks/${taskId}/review`,
    refresh,
  );
  const [busy, setBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const settings = useResource<components["schemas"]["ReviewerSettingsView"]>(
    `workspaces/${workspaceId}/reviewers/task?project=${projectId}&entity=${taskId}`,
    refresh,
  );
  const review = resource.data;
  async function act(action: "stop" | "retry" | "confirm-stopped") {
    if (!review) return;
    setBusy(true);
    try {
      const saved = await request<Review>(
        `projects/${projectId}/reviews/${review.id}/${action}`,
        "POST",
        { expected_revision: review.revision },
      );
      resource.setData(saved);
    } catch (error) {
      reportError(error, "Could not update review");
    } finally {
      setBusy(false);
    }
  }
  return (
    <DetailSection title="Independent review">
      <ContentStack>
        <ResourceRetry resources={[resource, settings]}>
          Retry review
        </ResourceRetry>
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
            {review.verdict && (
              <Disclosure summary="Review evidence">
                <Markdown>{review.verdict.evidence}</Markdown>
              </Disclosure>
            )}
            {review.problem && <p>{review.problem}</p>}
            {review.status === "waiting" && review.question_id && (
              <WorkspaceLink
                to={`/projects/${projectId}/tasks/${taskId}/conversation/question:${review.question_id}`}
              >
                Answer review question
              </WorkspaceLink>
            )}
            <WorkspaceLink
              to={`/projects/${projectId}/tasks/${taskId}/conversation/result:${review.result_id}`}
            >
              Reviewed result
            </WorkspaceLink>
            <div className="actions">
              {[
                "queued",
                "preparing",
                "running",
                "waiting",
                "uncertain",
              ].includes(review.status) && (
                <ConfirmButton
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  title="Stop this review?"
                  description="Retained findings and the task candidate are preserved. The queue remains unchanged."
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
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => void act("retry")}
                >
                  Retry review
                </Button>
              )}
            </div>
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
          </>
        )}
        {settings.data?.effective.enabled && !settings.data.effective.agent && (
          <Alert>
            <AlertDescription>
              Choose a task reviewer below to run automatic assessments.
            </AlertDescription>
          </Alert>
        )}
        <Disclosure
          summary="Task reviewer settings"
          onToggle={(event) => setSettingsOpen(event.currentTarget.open)}
        >
          <ReviewerSettings
            workspaceId={workspaceId}
            purpose="task"
            projectId={projectId}
            entity={taskId}
            refresh={refresh}
            onDirty={onDirty}
            active={settingsOpen}
          />
        </Disclosure>
      </ContentStack>
    </DetailSection>
  );
}

function ReviewRounds({
  projectId,
  review,
}: {
  projectId: string;
  review: Review;
}) {
  const page = usePage<components["schemas"]["ReviewAttemptPage"]>(
    `projects/${projectId}/reviews/${review.id}/attempts`,
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
          reviewId={review.id}
          attempt={attempt}
        />
      ))}
      {page.data?.next_before != null && (
        <Button
          size="sm"
          variant="outline"
          disabled={page.loadingMore}
          onClick={() => void page.older()}
        >
          Earlier rounds
        </Button>
      )}
    </ContentStack>
  );
}

function ReviewRound({
  projectId,
  reviewId,
  attempt,
}: {
  projectId: string;
  reviewId: string;
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
          <p>
            {attempt.problem ||
              (attempt.cleanup_confirmed
                ? "Process cleanup confirmed."
                : "Process cleanup needs confirmation.")}
          </p>
          <RunActivity
            projectId={projectId}
            runId={reviewId}
            review
            reviewAttemptId={attempt.id}
          />
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
        </ContentStack>
      )}
    </Disclosure>
  );
}
