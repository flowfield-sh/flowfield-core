import { requestHref } from "./requestLinks";
import { OpenInWorkTab } from "./WorkspaceTabs";
import { LoadMore } from "./LoadMore";
import { useContext, useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router";
import { ActivityEntry, ActivityBodyVisible } from "./ActivityEntry";
import { ContentStack, DetailSection } from "./DetailLayout";
import { OverlayFooter } from "./EntityOverlay";
import { Markdown } from "./Markdown";
import {
  MilestoneReport,
  MilestoneReviewResponse,
  reportState,
  verificationStatuses,
  type Verification,
} from "./MilestoneVerification";
import { ResourceRetry } from "./ResourceRetry";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { useFeedScroll } from "./useFeedScroll";
import { usePage, useResource } from "./useResource";
import { useRetainedDraftKeys, type DirtyCallback } from "./retainedState";
import { authorLabel, type Milestone } from "./workspace";

type Entry = {
  id: string;
  kind: "revision" | "review";
  number: number;
  timestamp: string;
  actor: string;
  status: string | null;
  verification_id: string | null;
};
type History = { items: Entry[]; next_cursor: string | null };

function RevisionBody({ path, revision }: { path: string; revision: number }) {
  const visible = useContext(ActivityBodyVisible);
  const resource = useResource<Milestone>(
    visible ? `${path}?revision=${revision}` : null,
    revision,
  );
  return (
    <>
      <ResourceRetry resources={[resource]} />
      {visible && !resource.data && !resource.error && <p>Loading revision…</p>}
      {resource.data && (
        <ContentStack space="section">
          <DetailSection title="Description">
            <Markdown>{resource.data.body || "No description yet."}</Markdown>
          </DetailSection>
          <DetailSection title="Acceptance criteria">
            <Markdown>
              {resource.data.acceptance_criteria ||
                "No acceptance criteria yet."}
            </Markdown>
          </DetailSection>
        </ContentStack>
      )}
    </>
  );
}

function ReviewBody({
  path,
  id,
  refresh,
  selected,
  milestoneId,
}: {
  path: string;
  id: string;
  refresh: unknown;
  selected?: Verification | null;
  milestoneId: string;
}) {
  const visible = useContext(ActivityBodyVisible);
  const resource = useResource<Verification>(
    visible && !selected ? `${path}/verifications/${id}` : null,
    refresh,
  );
  const value = selected ?? resource.data;
  return (
    <>
      <ResourceRetry resources={[resource]} />
      {visible && !value && !resource.error && <p>Loading review…</p>}
      {value?.milestone_id === milestoneId && (
        <ContentStack space="section">
          <MilestoneReport value={value} />
          {value.reviewed_by && (
            <DetailSection
              title={
                value.status === "accepted"
                  ? "Milestone accepted"
                  : value.promotion?.approved_at
                    ? "Promotion approved"
                    : "Changes requested"
              }
            >
              <p className="detail-metadata">
                {authorLabel(value.reviewed_by)} ·{" "}
                <Timestamp date={value.reviewed_at ?? value.updated_at} />
              </p>
              {value.review_note && <Markdown>{value.review_note}</Markdown>}
            </DetailSection>
          )}
        </ContentStack>
      )}
    </>
  );
}

export function MilestoneActivity({
  path,
  milestone,
  newest,
  refresh,
  onDirty,
}: {
  path: string;
  milestone: Milestone;
  newest?: string;
  refresh: unknown;
  onDirty: DirtyCallback;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const selected = params.get("verification") ?? newest;
  const milestonePath = `${path}/milestones/${milestone.id}`;
  const href = `/workspaces/${milestone.workspace_id}/milestones/${milestone.key}`;
  const version = JSON.stringify([milestone.revision, refresh]);
  const history = usePage<History>(
    `${milestonePath}/activity?limit=20`,
    version,
    true,
  );
  const report = useResource<Verification>(
    selected ? `${path}/verifications/${selected}` : null,
    version,
  );
  const value = report.data?.milestone_id === milestone.id ? report.data : null;
  useEffect(() => {
    if (!params.has("verification") && newest) {
      const next = new URLSearchParams(params);
      next.set("verification", newest);
      setParams(next, { replace: true, state: location.state });
    }
  }, [params, newest, setParams, location.state]);
  const root = useRef<HTMLDivElement>(null);
  const target = selected ? `verification:${selected}` : undefined;
  const scrolling = useFeedScroll(
    root,
    !!history.data,
    target,
    !selected || !!report.data,
  );
  const prefix = `${path}:milestone:${milestone.id}:verification:`;
  const drafts = useRetainedDraftKeys(prefix)
    .map((key) => key.slice(prefix.length))
    .filter((id) => id !== selected);
  const items = [...(history.data?.items ?? [])];
  if (value && !items.some((item) => item.verification_id === value.id))
    items.push({
      id: `verification:${value.id}`,
      kind: "review",
      number: 0,
      timestamp: value.created_at,
      actor: value.purpose === "review" ? "reviewer" : "worker",
      status: value.status,
      verification_id: value.id,
    });
  items.sort(
    (a, b) =>
      a.timestamp.localeCompare(b.timestamp) || a.id.localeCompare(b.id),
  );
  return (
    <ContentStack
      ref={root}
      space="section"
      role="tabpanel"
      aria-label="Activity"
    >
      <ResourceRetry resources={[history, report]} />
      {report.data && !value && (
        <p role="alert">This verification belongs to another milestone.</p>
      )}
      {selected && newest && selected !== newest && (
        <WorkspaceLink to={`?tab=activity&verification=${newest}`}>
          Latest review
        </WorkspaceLink>
      )}
      {drafts.map((id) => (
        <WorkspaceLink key={id} to={`?tab=activity&verification=${id}`}>
          Unsent review note
        </WorkspaceLink>
      ))}
      {history.data?.next_cursor && (
        <LoadMore
          size="sm"
          variant="outline"
          disabled={history.loadingMore || history.loading}
          onClick={() => {
            scrolling.readingEarlier();
            void history.older();
          }}
        >
          Earlier activity
        </LoadMore>
      )}
      {!history.data && !history.error && <p>Loading activity…</p>}
      <ol className="conversation-messages" aria-label="Milestone activity">
        {items.map((item) => {
          const active = item.verification_id === selected;
          const link =
            item.kind === "revision"
              ? `${href}/revisions/${item.number}`
              : `${href}?tab=activity&verification=${item.verification_id}`;
          return (
            <li
              key={item.id}
              data-message-id={item.id}
              data-kind={item.kind}
              data-announcement-source={
                item.verification_id
                  ? `workspace:${milestone.workspace_id}:milestone:${item.verification_id}`
                  : undefined
              }
            >
              <ActivityEntry
                identity={`${milestone.workspace_id}:${milestone.id}:${item.id}`}
                selected={active}
                hasBody
                title={
                  item.kind === "revision"
                    ? `Milestone ${item.number === 1 ? "defined" : "updated"} · revision ${item.number}`
                    : (verificationStatuses[item.status ?? ""] ??
                      "Milestone review")
                }
                state={
                  active && value
                    ? reportState(value)
                    : {
                        label: item.status
                          ? verificationStatuses[item.status]
                          : "Recorded",
                        tone:
                          item.status === "accepted"
                            ? "complete"
                            : item.status === "failed"
                              ? "attention"
                              : "idle",
                      }
                }
                metadata={
                  <>
                    {item.actor === "reviewer"
                      ? "Reviewer"
                      : authorLabel(item.actor)}{" "}
                    ·{" "}
                    <WorkspaceLink to={link}>
                      <Timestamp date={item.timestamp} />
                    </WorkspaceLink>
                    {item.verification_id && (
                      <OpenInWorkTab
                        href={requestHref(
                          milestone.workspace_id,
                          "milestone",
                          item.verification_id,
                        )}
                        label="Open request in work tab"
                      />
                    )}
                  </>
                }
              >
                {item.kind === "revision" ? (
                  <RevisionBody path={milestonePath} revision={item.number} />
                ) : (
                  <ReviewBody
                    path={path}
                    id={item.verification_id!}
                    refresh={version}
                    selected={active ? value : null}
                    milestoneId={milestone.id}
                  />
                )}
              </ActivityEntry>
            </li>
          );
        })}
      </ol>
      {value && (
        <OverlayFooter>
          <MilestoneReviewResponse
            key={value.id}
            value={value}
            path={path}
            refresh={report.retry}
            onDirty={onDirty}
            allowReview={value.id === newest}
          />
        </OverlayFooter>
      )}
    </ContentStack>
  );
}
