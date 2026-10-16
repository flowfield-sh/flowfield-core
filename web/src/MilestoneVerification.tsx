import { useRetainedState, retainDirty } from "./retainedState";
import type { DirtyCallback } from "./retainedState";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  lazy,
  Suspense,
  type ReactNode,
} from "react";
import { useLocation, useSearchParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "./api-schema";
import {
  ContentStack,
  DetailHeading,
  DetailSection,
  Disclosure,
  ResponseInput,
} from "./DetailLayout";
import { Markdown } from "./Markdown";
import { WorkState } from "./WorkState";
import { Inspection } from "./Inspection";
import type { DiffViewState } from "./CodeDiff";
import { CheckEvidence } from "./Integration";
import { OverlayFooter } from "./EntityOverlay";
import { useFeedScroll } from "./useFeedScroll";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { usePage, useResource } from "./useResource";
import { label, request } from "./workspace";
import {
  MilestoneReviewRecord,
  MilestoneReviewRecovery,
} from "./MilestoneReviewSettings";

export type Verification = components["schemas"]["MilestoneVerification"];
const CodeDiff = lazy(() => import("./CodeDiff"));
type History = {
  items: (Pick<Verification, "id" | "status" | "updated_at"> & {
    number: number;
  })[];
  next_cursor: number | null;
};
const statuses: Record<string, string> = {
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
    `${path}:verification:${value.id}:revision`,
    value.revision,
  );
  const [note, setNote] = useRetainedState(
    `${path}:verification:${value.id}:note`,
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
    retainDirty(`${path}:verification:${value.id}`, !!note.trim());
    onDirty(!!note.trim() || pending, !pending);
    return () => onDirty(false);
  }, [note, pending, onDirty, path, value.id]);
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
              Deliver the verified result to{" "}
              <code>{value.promotion.target_branch}</code> in the project
              checkout.
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

function reportState(value: Verification): components["schemas"]["WorkState"] {
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
        : statuses[value.status],
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

export function MilestoneReport({
  value,
  metadata,
}: {
  value: Verification;
  metadata?: ReactNode;
}) {
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
          <li className="content-stack" key={index} data-space="tight">
            <DetailHeading
              entry
              titleAs="h3"
              title={
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
            />
            <div>
              {finding ? (
                <Markdown>{finding.evidence}</Markdown>
              ) : (
                <p>No finding yet.</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
  return (
    <article className="conversation-message">
      <DetailHeading
        entry
        inlineMetadata
        titleAs="h3"
        metadata={metadata}
        title={
          <span className="feed-event-title">
            <WorkState indicatorOnly state={reportState(value)} />
            <span>
              {value.report ? "Verification report" : "Verification started"}
            </span>
          </span>
        }
      />
      <ContentStack className="conversation-message-body" space="section">
        <DetailSection title="Details">
          <ContentStack space="flush">
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
                    Combined candidate:{" "}
                    <code>{promotion.candidate_commit ?? "Preparing"}</code>
                  </p>
                  <p>
                    Milestone source: <code>{promotion.source_commit}</code>
                  </p>
                  <p>
                    Destination before promotion:{" "}
                    <code>{promotion.target_before}</code>
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
            {value.limitations && (
              <Disclosure summary="Limitations reported">
                <Markdown>{value.limitations}</Markdown>
              </Disclosure>
            )}
            <Disclosure summary="Verification context">
              <ContentStack space="section">
                <p>
                  Criteria checked:{" "}
                  <WorkspaceLink
                    to={`/workspaces/${value.workspace_id}/milestones/${value.milestone_key}/revisions/${value.milestone_revision}`}
                  >
                    revision {value.milestone_revision}
                  </WorkspaceLink>
                </p>
                {value.report?.coverage_notes && (
                  <DetailSection title="Coverage">
                    <Markdown>{value.report.coverage_notes}</Markdown>
                  </DetailSection>
                )}
                <DetailSection title="Acceptance criteria">
                  <Markdown>{value.criteria}</Markdown>
                </DetailSection>
              </ContentStack>
            </Disclosure>
            <Disclosure summary="Tasks">
              <ContentStack aria-label="Verification tasks">
                <p>
                  Contributions:{" "}
                  {value.tasks.map((task, index) => (
                    <span key={`${task.project_id}/${task.task_id}`}>
                      {index > 0 && ", "}
                      <WorkspaceLink
                        to={`/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`}
                      >
                        {task.task_key}
                      </WorkspaceLink>
                    </span>
                  ))}
                </p>
                {value.task_key && (
                  <p>
                    Verification:{" "}
                    <WorkspaceLink
                      to={`/projects/${value.project_id}/tasks/${value.task_key}${value.result_id ? `/result/${value.result_id}` : ""}`}
                    >
                      {value.task_key}
                    </WorkspaceLink>
                  </p>
                )}
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
        <DetailSection title="Checks">{checks}</DetailSection>
      </ContentStack>
    </article>
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
          <OverlayFooter>
            <MilestoneReviewResponse
              key={value.id}
              value={value}
              path={path}
              refresh={resource.retry}
              onDirty={onDirty}
            />
          </OverlayFooter>
        </>
      )}
    </ContentStack>
  );
}

function ReviewEntry({
  id,
  path,
  refresh,
  selected,
  markDirty,
  milestoneId,
  respond,
}: {
  respond: boolean;
  id: string;
  path: string;
  refresh: unknown;
  selected: boolean;
  markDirty: (id: string, value: boolean, retained?: boolean) => void;
  milestoneId: string;
}) {
  const resource = useResource<Verification>(
    `${path}/verifications/${id}`,
    refresh,
  );
  const value = resource.data;
  const dirty = useCallback(
    (value: boolean, retained?: boolean) => markDirty(id, value, retained),
    [id, markDirty],
  );
  const [hasDraft, setHasDraft] = useState(false);
  const onDirty = useCallback(
    (value: boolean, retained?: boolean) => {
      setHasDraft(value);
      dirty(value, retained);
    },
    [dirty],
  );
  if (!value)
    return (
      <li data-message-id={id}>
        <ResourceRetry resources={[resource]} />
        {!resource.error && <p>Loading review…</p>}
      </li>
    );
  if (value.milestone_id !== milestoneId)
    return (
      <li role="alert">This verification belongs to another milestone.</li>
    );
  const href = `/workspaces/${value.workspace_id}/milestones/${value.milestone_key}?verification=${value.id}`;
  return (
    <li
      data-message-id={id}
      data-announcement-source={`workspace:${value.workspace_id}:milestone:${value.id}`}
    >
      <MilestoneReport
        value={value}
        metadata={
          <>
            {value.purpose === "review" ? "Reviewer" : "Worker"} ·{" "}
            <WorkspaceLink to={href}>
              <Timestamp date={value.created_at} />
            </WorkspaceLink>
          </>
        }
      />
      {value.reviewed_by && (
        <article className="conversation-message review-decision">
          <DetailHeading
            entry
            inlineMetadata
            titleAs="h3"
            title={
              value.status === "accepted"
                ? "Milestone accepted"
                : value.promotion?.approved_at
                  ? "Promotion approved"
                  : "Changes requested"
            }
            metadata={
              <>
                {value.reviewed_by === "human" ? "You" : value.reviewed_by} ·{" "}
                <Timestamp date={value.reviewed_at ?? value.updated_at} />
              </>
            }
          />
          {value.review_note && (
            <div className="conversation-message-body">
              <Markdown>{value.review_note}</Markdown>
            </div>
          )}
        </article>
      )}
      {(selected || respond || hasDraft) && (
        <OverlayFooter>
          <MilestoneReviewResponse
            key={value.id}
            value={value}
            path={path}
            refresh={resource.retry}
            onDirty={onDirty}
            allowReview={respond}
          />
        </OverlayFooter>
      )}
    </li>
  );
}

export function MilestoneVerification({
  path,
  milestoneId,
  revision,
  refresh,
  onDirty,
}: {
  path: string;
  milestoneId: string;
  milestoneKey: string;
  revision: number;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const selected = params.get("verification");
  const history = usePage<History>(
    `${path}/milestones/${milestoneId}/verifications?limit=10`,
    JSON.stringify([revision, refresh]),
  );
  const newest = history.data?.items[0]?.id;
  useEffect(() => {
    if (!selected && newest) {
      const next = new URLSearchParams(params);
      next.set("verification", newest);
      setParams(next, { replace: true, state: location.state });
    }
  }, [newest, selected, params, setParams, location.state]);
  const markDirty = useDirtyItems(onDirty);
  const root = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState<History | null>(null);
  const [entries, setEntries] = useState<History["items"]>([]);
  if (history.data && history.data !== seen) {
    setSeen(history.data);
    setEntries((previous) =>
      [
        ...new Map(
          [...previous, ...history.data!.items].map((item) => [item.id, item]),
        ).values(),
      ].sort((a, b) => a.number - b.number),
    );
  }
  const ids = entries.map((item) => item.id);
  if (selected && !ids.includes(selected)) ids.unshift(selected);
  const scrolling = useFeedScroll(
    root,
    !!history.data,
    selected ?? undefined,
    !!history.data,
  );
  return (
    <ContentStack
      ref={root}
      space="section"
      className="milestone-review-feed"
      role="tabpanel"
      aria-label="Review"
    >
      <ResourceRetry resources={[history]} />
      {selected && newest && selected !== newest && (
        <WorkspaceLink to={`?tab=review&verification=${newest}`}>
          Latest review
        </WorkspaceLink>
      )}
      {!history.data && !history.error && <p>Loading reviews…</p>}
      {history.data?.items.length === 0 && !selected && (
        <p>
          No reviews yet. Verification will appear here when the contributing
          work is ready.
        </p>
      )}
      {history.data?.next_cursor && (
        <Button
          size="sm"
          variant="outline"
          disabled={history.loadingMore}
          onClick={() => {
            scrolling.readingEarlier();
            void history.older();
          }}
        >
          Earlier reviews
        </Button>
      )}
      <ol className="conversation-messages" aria-label="Milestone review feed">
        {ids.map((id) => (
          <ReviewEntry
            key={id}
            id={id}
            path={path}
            refresh={JSON.stringify([revision, refresh])}
            selected={selected === id}
            respond={
              id === history.data?.items[0]?.id &&
              (!selected || selected === id)
            }
            markDirty={markDirty}
            milestoneId={milestoneId}
          />
        ))}
      </ol>
    </ContentStack>
  );
}
