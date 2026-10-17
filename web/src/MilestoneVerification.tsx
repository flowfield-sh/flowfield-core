import { useRetainedState, retainDirty } from "./retainedState";
import type { DirtyCallback } from "./retainedState";
import { useEffect, useState, lazy, Suspense } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "./api-schema";
import {
  ContentStack,
  DetailSection,
  Disclosure,
  ResponseInput,
} from "./DetailLayout";
import { Markdown } from "./Markdown";
import { ResultFiles } from "./Files";
import { WorkState } from "./WorkState";
import { Inspection } from "./Inspection";
import type { DiffViewState } from "./CodeDiff";
import { CheckEvidence } from "./Integration";
import { PaneFooter } from "./EntityPane";
import { ResourceRetry } from "./ResourceRetry";
import { WorkspaceLink } from "./WorkspaceLink";
import { WorkIdentity } from "./WorkIdentity";
import { Timestamp } from "./Timestamp";
import { LoadMore } from "./LoadMore";
import { usePage, useResource } from "./useResource";
import { label, request } from "./workspace";
import {
  MilestoneReviewRecord,
  MilestoneReviewRecovery,
} from "./MilestoneReviewSettings";

export type Verification = components["schemas"]["MilestoneVerification"];
const CodeDiff = lazy(() => import("./CodeDiff"));
export const verificationStatuses: Record<string, string> = {
  preparing: "Preparing combined candidate",
  promoting: "Promoting verified milestone",
  delivery_blocked: "Promotion needs your attention",
  verifying: "Verification in progress",
  ready: "Ready for milestone review",
  failed: "Verification needs follow-up",
  stale: "Verification out of date",
  accepted: "Milestone accepted",
  changes_requested: "Changes requested",
};

export function MilestoneReviewResponse({
  value,
  path,
  refresh,
  onDirty,
  allowReview = true,
}: {
  value: Verification;
  allowReview?: boolean;
  path: string;
  refresh: () => void;
  onDirty: DirtyCallback;
}) {
  const [revision, setRevision] = useRetainedState(
    `${path}:milestone:${value.milestone_id}:verification:${value.id}:revision`,
    value.revision,
  );
  const [note, setNote] = useRetainedState(
    `${path}:milestone:${value.milestone_id}:verification:${value.id}:note`,
    "",
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!note && !pending && value.revision !== revision)
    setRevision(value.revision);
  const stale = value.revision !== revision;
  const canReview = allowReview && value.current && value.status === "ready";
  const canRetry =
    allowReview && value.current && value.status === "delivery_blocked";
  useEffect(() => {
    retainDirty(
      `${path}:milestone:${value.milestone_id}:verification:${value.id}`,
      !!note.trim(),
    );
    onDirty(!!note.trim() || pending, !pending);
    return () => onDirty(false);
  }, [note, pending, onDirty, path, value.id, value.milestone_id]);
  async function review(
    action: "accept" | "request_changes" | "retry_promotion",
  ) {
    if (
      pending ||
      stale ||
      !(action === "retry_promotion" ? canRetry : canReview) ||
      (action === "request_changes" && !note.trim())
    )
      return;
    setPending(true);
    setError(null);
    try {
      await request(`${path}/verifications/${value.id}/review`, "POST", {
        expected_revision: revision,
        action,
        note,
      });
      setNote("");
      refresh();
    } catch (error) {
      setError((error as Error).message);
      refresh();
    } finally {
      setPending(false);
    }
  }
  const reviewRecovery =
    allowReview &&
    value.current &&
    value.review_id &&
    value.review_status &&
    [
      "queued",
      "preparing",
      "running",
      "waiting",
      "failed",
      "uncertain",
      "stopped",
    ].includes(value.review_status);
  const hint =
    allowReview && !reviewRecovery
      ? (value.problem ??
        (value.status === "failed" || value.status === "changes_requested"
          ? "Ask the Coordinator to address the findings and verify the milestone again."
          : value.status === "stale"
            ? "The work changed after this report. Ask the Coordinator to verify the updated milestone."
            : null))
      : null;
  if (
    !canReview &&
    !canRetry &&
    !reviewRecovery &&
    !note.trim() &&
    !hint &&
    !error
  )
    return null;
  return (
    <ContentStack className="task-input" aria-label="Milestone response">
      {hint && <p>{hint}</p>}
      {reviewRecovery && value.review_id && (
        <MilestoneReviewRecovery
          projectId={value.project_id}
          reviewId={value.review_id}
          refresh={value.revision}
        />
      )}
      {canRetry && (
        <div className="actions">
          <Button
            size="sm"
            disabled={pending || stale}
            onClick={() => void review("retry_promotion")}
          >
            Retry promotion
          </Button>
        </div>
      )}
      {(canReview || !!note.trim()) && (
        <ContentStack aria-label="Your review">
          {canReview && value.promotion && (
            <p>
              Destination: <code>{value.promotion.target_branch}</code>
            </p>
          )}
          <ResponseInput>
            <Textarea
              aria-label="Review note"
              rows={2}
              maxLength={8000}
              id={`review-${value.id}`}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              disabled={pending}
              placeholder="Review note — required when requesting changes"
            />
          </ResponseInput>
          {stale && (
            <p role="alert">
              {canReview
                ? "This verification changed. Reopen it before reviewing."
                : "This request no longer needs a response. Your unsent note is preserved."}
            </p>
          )}
          <div className="actions">
            {canReview && (
              <>
                <Button
                  size="sm"
                  disabled={pending || stale}
                  onClick={() => void review("accept")}
                >
                  {value.promotion_id
                    ? "Accept and promote"
                    : "Accept milestone"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={pending || stale || !note.trim()}
                  onClick={() => void review("request_changes")}
                >
                  Request changes
                </Button>
              </>
            )}
            {note && (
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => setNote("")}
              >
                Discard note
              </Button>
            )}
          </div>
        </ContentStack>
      )}
      {error && <p role="alert">{error}</p>}
    </ContentStack>
  );
}

export function reportState(
  value: Verification,
): components["schemas"]["WorkState"] {
  if (value.status === "verifying" && value.review_status === "waiting")
    return { label: "Waiting for your answer", tone: "waiting" };
  if (value.status === "verifying" && value.review_status === "queued")
    return { label: "Milestone review queued", tone: "waiting" };
  const passed = value.report?.findings.filter(
    (f) => f.verdict === "passed",
  ).length;
  return {
    label: [
      value.status === "accepted" && !value.current
        ? "Previously accepted"
        : verificationStatuses[value.status],
      passed != null
        ? `${passed} of ${value.checks.length} checks passed`
        : null,
      value.report?.coverage === "incomplete" ? "Coverage incomplete" : null,
    ]
      .filter(Boolean)
      .join(" · "),
    tone: ["preparing", "verifying", "promoting"].includes(value.status)
      ? "active"
      : ["failed", "delivery_blocked"].includes(value.status)
        ? "attention"
        : value.status === "accepted"
          ? value.current
            ? "complete"
            : "idle"
          : "waiting",
  };
}

export function MilestoneReport({ value }: { value: Verification }) {
  const [diffView, setDiffView] = useState<DiffViewState>({
    selected: 0,
    mode: "unified",
  });
  const promotion = value.promotion;
  const findings = value.report?.findings ?? [];
  const checks = (
    <ol className="verification-checks" aria-label="Checks">
      {value.checks.map((check, index) => {
        const finding = findings.find((f) => f.check === index + 1);
        const verdict = finding ? label(finding.verdict) : "Pending";
        return (
          <li key={index}>
            <Disclosure
              open={finding?.verdict !== "passed"}
              summary={
                <span className="feed-event-title">
                  <WorkState
                    indicatorOnly
                    state={{
                      label: verdict,
                      tone:
                        finding?.verdict === "passed"
                          ? "complete"
                          : finding?.verdict === "failed"
                            ? "attention"
                            : "waiting",
                    }}
                  />
                  <span>{check}</span>
                </span>
              }
            >
              {finding ? (
                <Markdown>{finding.evidence}</Markdown>
              ) : (
                <p>No finding yet.</p>
              )}
            </Disclosure>
          </li>
        );
      })}
    </ol>
  );
  return (
    <article>
      <ContentStack space="section">
        {value.summary && <Markdown>{value.summary}</Markdown>}
        {value.limitations && (
          <DetailSection title="Limitations">
            <Markdown>{value.limitations}</Markdown>
          </DetailSection>
        )}
        <ResultFiles files={value.files} />
        <DetailSection title="Checks">{checks}</DetailSection>
        <MilestoneHumanTesting value={value} />
        <DetailSection title="Tasks">
          <ul className="work-resource-list" aria-label="Verification tasks">
            {value.tasks.map((task) => (
              <li key={task.result_id}>
                <WorkspaceLink
                  className="work-navigation-row"
                  to={`/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`}
                >
                  <WorkIdentity
                    state={{ label: "Delivered result", tone: "complete" }}
                    itemKey={task.task_key}
                    title={
                      value.task_titles?.[task.result_id] ??
                      "Contributing result"
                    }
                  />
                </WorkspaceLink>
              </li>
            ))}
            {value.task_key && (
              <li>
                <WorkspaceLink
                  className="work-navigation-row"
                  to={`/projects/${value.project_id}/tasks/${value.task_key}${value.result_id ? `/result/${value.result_id}` : ""}`}
                >
                  <WorkIdentity itemKey={value.task_key} title="Verification" />
                </WorkspaceLink>
              </li>
            )}
          </ul>
        </DetailSection>
        <DetailSection title="Details">
          <ContentStack>
            {promotion?.candidate_commit && (
              <>
                <Inspection
                  projectId={value.project_id}
                  milestone={value}
                  refresh={value.revision}
                />
                <Disclosure summary="Code changes">
                  <Suspense fallback={<p>Loading changes…</p>}>
                    <CodeDiff
                      path={`workspaces/${value.workspace_id}/verifications/${value.id}`}
                      result={promotion.candidate_commit}
                      view={diffView}
                      changeView={setDiffView}
                    />
                  </Suspense>
                </Disclosure>
              </>
            )}
            {promotion && (
              <Disclosure summary="Delivery evidence">
                <ContentStack>
                  <p>
                    Destination: <code>{promotion.target_branch}</code>
                  </p>
                  <p>
                    Candidate:{" "}
                    <code className="wrap-anywhere">
                      {promotion.candidate_commit ?? "Preparing"}
                    </code>
                  </p>
                  <p>
                    Milestone source:{" "}
                    <code className="wrap-anywhere">
                      {promotion.source_commit}
                    </code>
                  </p>
                  <p>
                    Destination before:{" "}
                    <code className="wrap-anywhere">
                      {promotion.target_before}
                    </code>
                  </p>
                  <CheckEvidence
                    checks={promotion.setup_checks}
                    title="Runtime setup"
                  />
                  <CheckEvidence
                    checks={promotion.checks}
                    title="Project checks"
                  />
                </ContentStack>
              </Disclosure>
            )}
            <Disclosure summary="Verification context">
              <ContentStack>
                <p>
                  Criteria checked:{" "}
                  <WorkspaceLink
                    to={`/workspaces/${value.workspace_id}/milestones/${value.milestone_key}/revisions/${value.milestone_revision}`}
                  >
                    revision {value.milestone_revision}
                  </WorkspaceLink>
                </p>
                {value.report?.coverage_notes && (
                  <Disclosure summary="Coverage">
                    <Markdown>{value.report.coverage_notes}</Markdown>
                  </Disclosure>
                )}
                <Disclosure summary="Acceptance criteria">
                  <Markdown>{value.criteria}</Markdown>
                </Disclosure>
              </ContentStack>
            </Disclosure>
            {value.review_id && (
              <MilestoneReviewRecord
                projectId={value.project_id}
                reviewId={value.review_id}
                refresh={value.revision}
              />
            )}
          </ContentStack>
        </DetailSection>
      </ContentStack>
    </article>
  );
}

function MilestoneHumanTesting({ value }: { value: Verification }) {
  const notes = usePage<components["schemas"]["MilestoneTestingPage"]>(
    `workspaces/${value.workspace_id}/verifications/${value.id}/testing`,
    value,
    true,
  );
  if (!notes.data?.items.length && !notes.error) return null;
  return (
    <DetailSection title="Human testing" aria-label="Human testing">
      <ResourceRetry resources={[notes]} />
      {notes.data?.items.map((note) => (
        <Disclosure
          key={note.id}
          summary={
            <span>
              {note.actor.label} · {note.task_key} ·{" "}
              <Timestamp date={note.created_at} />
            </span>
          }
        >
          <Markdown>{note.body}</Markdown>
        </Disclosure>
      ))}
      {notes.data?.next_offset != null && (
        <LoadMore
          disabled={notes.loadingMore}
          onClick={() => void notes.older()}
        >
          Load earlier testing
        </LoadMore>
      )}
    </DetailSection>
  );
}

export function MilestoneReviewRequest({
  path,
  id,
  refresh,
  onDirty,
}: {
  path: string;
  id: string;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const resource = useResource<Verification>(
    `${path}/verifications/${id}`,
    refresh,
  );
  const value = resource.data;
  return (
    <ContentStack
      data-announcement-source={
        value
          ? `workspace:${value.workspace_id}:milestone:${value.id}`
          : undefined
      }
    >
      <ResourceRetry resources={[resource]} />
      {!value && !resource.error && <p>Loading review…</p>}
      {value && (
        <>
          <MilestoneReport value={value} />
          {value.review_note && <Markdown>{value.review_note}</Markdown>}
          <PaneFooter>
            <MilestoneReviewResponse
              key={value.id}
              value={value}
              path={path}
              refresh={resource.retry}
              onDirty={onDirty}
            />
          </PaneFooter>
        </>
      )}
    </ContentStack>
  );
}
