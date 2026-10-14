import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "./api-schema";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { DetailHeading } from "./DetailLayout";
import { OverlayFooter } from "./EntityOverlay";
import { useFeedScroll } from "./useFeedScroll";
import { useDirtyItems } from "./useDirtyItems";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { usePage, useResource } from "./useResource";
import { label, request } from "./workspace";

export type Verification = components["schemas"]["MilestoneVerification"];
type History = {
  items: (Pick<Verification, "id" | "status" | "updated_at"> & {
    number: number;
  })[];
  next_cursor: number | null;
};
const statuses: Record<string, string> = {
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
}: {
  value: Verification;
  path: string;
  refresh: () => void;
  onDirty: (dirty: boolean) => void;
}) {
  const [revision, setRevision] = useState(value.revision);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!note && !pending && value.revision !== revision)
    setRevision(value.revision);
  const stale = value.revision !== revision;
  useEffect(() => {
    onDirty(!!note.trim() || pending);
    return () => onDirty(false);
  }, [note, pending, onDirty]);
  async function review(action: "accept" | "request_changes") {
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
  return (
    <ContentStack>
      {(value.status === "ready" || !!note.trim()) && (
        <ContentStack className="review-response" aria-label="Your review">
          <p className="detail-metadata">
            Acceptance records your review of these delivered versions. It does
            not promote code.
          </p>
          <label htmlFor={`review-${value.id}`}>Review note</label>
          <Textarea
            id={`review-${value.id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            disabled={pending}
            placeholder="Required when requesting changes"
          />
          {stale && (
            <p role="alert">
              This verification changed. Reopen it before reviewing.
            </p>
          )}
          <div className="actions justify-end">
            <Button
              disabled={
                pending || stale || value.status !== "ready" || !value.current
              }
              onClick={() => void review("accept")}
            >
              Accept milestone
            </Button>
            <Button
              variant="outline"
              disabled={
                pending ||
                stale ||
                value.status !== "ready" ||
                !value.current ||
                !note.trim()
              }
              onClick={() => void review("request_changes")}
            >
              Request changes
            </Button>
            {note && (
              <Button
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

export function MilestoneReport({ value }: { value: Verification }) {
  const findings = value.report?.findings ?? [];
  const passed = findings.filter((f) => f.verdict === "passed").length;
  return (
    <ContentStack>
      {value.problem && <p role="status">{value.problem}</p>}
      {value.report ? (
        <>
          <p>
            <strong>
              {passed} of {value.checks.length} checks passed
            </strong>{" "}
            · Coverage: {label(value.report.coverage)}
          </p>
          <Markdown>{value.report.coverage_notes}</Markdown>
        </>
      ) : (
        <p>The verifier is checking the combined outcome.</p>
      )}
      {value.limitations && (
        <DetailSection title="Reported limitations">
          <Markdown>{value.limitations}</Markdown>
        </DetailSection>
      )}
      <Disclosure summary="Criteria and findings">
        <ContentStack>
          <Markdown>{value.criteria}</Markdown>
          <WorkspaceLink
            to={`/workspaces/${value.workspace_id}/milestones/${value.milestone_key}/revisions/${value.milestone_revision}`}
          >
            Milestone revision {value.milestone_revision}
          </WorkspaceLink>
          {value.checks.map((check, index) => {
            const finding = findings.find((f) => f.check === index + 1);
            return (
              <ContentStack key={index}>
                <strong>
                  {check} · {finding ? label(finding.verdict) : "Pending"}
                </strong>
                {finding && <Markdown>{finding.evidence}</Markdown>}
              </ContentStack>
            );
          })}
        </ContentStack>
      </Disclosure>
      <Disclosure summary={`${value.tasks.length} delivered contributions`}>
        <ContentStack>
          {value.tasks.map((task) => (
            <div key={`${task.project_id}/${task.task_id}`}>
              <WorkspaceLink
                to={`/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`}
              >
                {task.project_id} / {task.task_key}
              </WorkspaceLink>
              <span className="detail-metadata">
                {" "}
                · {task.commit.slice(0, 12)}
                {task.target_branch ? ` → ${task.target_branch}` : " · Report"}
              </span>
            </div>
          ))}
          <WorkspaceLink
            to={`/projects/${value.project_id}/tasks/${value.task_key}${value.result_id ? `/result/${value.result_id}` : ""}`}
          >
            View verifier task {value.task_key}
          </WorkspaceLink>
        </ContentStack>
      </Disclosure>
    </ContentStack>
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
  onDirty: (dirty: boolean) => void;
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
          <h3 className="detail-title">
            {value.status === "ready" && value.current
              ? `Accept ${value.title}?`
              : statuses[value.status]}
          </h3>
          <MilestoneReport value={value} />
          {value.review_note && <Markdown>{value.review_note}</Markdown>}
          <MilestoneReviewResponse
            key={value.id}
            value={value}
            path={path}
            refresh={resource.retry}
            onDirty={onDirty}
          />
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
  latest,
  markDirty,
  milestoneId,
  respond,
}: {
  respond: boolean;
  id: string;
  path: string;
  refresh: unknown;
  selected: boolean;
  latest: boolean;
  markDirty: (id: string, value: boolean) => void;
  milestoneId: string;
}) {
  const resource = useResource<Verification>(
    `${path}/verifications/${id}`,
    refresh,
  );
  const value = resource.data;
  const dirty = useCallback(
    (value: boolean) => markDirty(id, value),
    [id, markDirty],
  );
  const [hasDraft, setHasDraft] = useState(false);
  const onDirty = useCallback(
    (value: boolean) => {
      setHasDraft(value);
      dirty(value);
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
      <article className="conversation-message">
        <DetailHeading
          entry
          inlineMetadata
          title={value.report ? "Verification report" : "Verification started"}
          metadata={
            <>
              Worker ·{" "}
              <WorkspaceLink to={href}>
                <Timestamp date={value.created_at} />
              </WorkspaceLink>
            </>
          }
        />
        <ContentStack className="conversation-message-body">
          <strong>
            {value.status === "accepted" && !value.current
              ? "Previously accepted"
              : statuses[value.status]}
          </strong>
          {latest || selected ? (
            <MilestoneReport value={value} />
          ) : (
            <Disclosure summary="View report">
              <MilestoneReport value={value} />
            </Disclosure>
          )}
        </ContentStack>
      </article>
      {value.reviewed_by && (
        <article className="conversation-message review-decision">
          <DetailHeading
            entry
            inlineMetadata
            titleAs="h3"
            title={
              value.status === "accepted"
                ? "Milestone accepted"
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
      {(respond || hasDraft) && (
        <OverlayFooter>
          <MilestoneReviewResponse
            key={value.id}
            value={value}
            path={path}
            refresh={resource.retry}
            onDirty={onDirty}
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
  onDirty: (dirty: boolean) => void;
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
            latest={id === history.data?.items[0]?.id}
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
