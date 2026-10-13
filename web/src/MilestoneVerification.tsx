import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "./api-schema";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { Markdown } from "./Markdown";
import { StatusNotice } from "./StatusNotice";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { label, request } from "./workspace";

type Verification = components["schemas"]["MilestoneVerification"];
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

function Review({
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
  const [revision] = useState(value.revision);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    <ContentStack
      data-announcement-source={`workspace:${value.workspace_id}:milestone:${value.id}`}
    >
      <StatusNotice
        title={
          value.status === "accepted" && !value.current
            ? "Previously accepted"
            : statuses[value.status]
        }
        tone={
          value.status === "ready"
            ? "attention"
            : value.status === "accepted" && value.current
              ? "success"
              : "neutral"
        }
      >
        {value.problem && <p>{value.problem}</p>}
        {value.status === "ready" && (
          <p>
            Compare the findings with the full acceptance criteria. Acceptance
            records your review of these delivered versions.
          </p>
        )}
        {value.status === "verifying" && (
          <p>
            The report task is checking the whole outcome. A completed task does
            not itself accept the milestone.
          </p>
        )}
        {value.status === "failed" && (
          <p>
            Ask the coordinator to address failed checks or gaps in coverage and
            prepare another verification.
          </p>
        )}
        {value.reviewed_by && (
          <p>
            {value.reviewed_by} ·{" "}
            <Timestamp date={value.reviewed_at ?? value.updated_at} />
          </p>
        )}
        {value.review_note && <Markdown>{value.review_note}</Markdown>}
      </StatusNotice>
      <p className="detail-metadata">
        Milestone revision {value.milestone_revision} ·{" "}
        <Timestamp date={value.created_at} />
      </p>
      <Disclosure summary="Acceptance criteria used for this verification">
        <Markdown>{value.criteria}</Markdown>
        <WorkspaceLink
          to={`/workspaces/${value.workspace_id}/milestones/${value.milestone_key}/revisions/${value.milestone_revision}`}
        >
          View milestone revision {value.milestone_revision}
        </WorkspaceLink>
      </Disclosure>
      <DetailSection title="Findings">
        {value.checks.map((check, index) => {
          const finding = value.report?.findings.find(
            (f) => f.check === index + 1,
          );
          return (
            <ContentStack key={index}>
              <strong>
                {index + 1}. {check} ·{" "}
                {finding ? label(finding.verdict) : "Pending"}
              </strong>
              {finding && <Markdown>{finding.evidence}</Markdown>}
            </ContentStack>
          );
        })}
        {value.report && (
          <ContentStack>
            <strong>Coverage: {label(value.report.coverage)}</strong>
            <Markdown>{value.report.coverage_notes}</Markdown>
          </ContentStack>
        )}
      </DetailSection>
      {value.limitations && (
        <DetailSection title="Reported limitations">
          <Markdown>{value.limitations}</Markdown>
        </DetailSection>
      )}
      <Disclosure summary={`${value.tasks.length} delivered contributions`}>
        {value.tasks.map((task) => (
          <ContentStack key={`${task.project_id}/${task.task_id}`}>
            <WorkspaceLink
              to={`/projects/${task.project_id}/tasks/${task.task_key}/result/${task.result_id}`}
            >
              {task.project_id} / {task.task_key}
            </WorkspaceLink>
            <span className="detail-metadata">
              Task revision {task.revision} · {task.commit.slice(0, 12)}
              {task.target_branch ? ` · ${task.target_branch}` : " · Report"}
            </span>
          </ContentStack>
        ))}
        <WorkspaceLink
          to={`/projects/${value.project_id}/tasks/${value.task_key}${value.result_id ? `/result/${value.result_id}` : ""}`}
        >
          View verifier task {value.task_key}
        </WorkspaceLink>
      </Disclosure>
      {(value.status === "ready" || !!note.trim()) && (
        <DetailSection title="Your review">
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
        </DetailSection>
      )}
      {error && <p role="alert">{error}</p>}
    </ContentStack>
  );
}

export function MilestoneVerification({
  path,
  milestoneId,
  milestoneKey,
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
  const selected = params.get("verification");
  const [before, setBefore] = useState<number | null>(null);
  const history = useResource<History>(
    `${path}/milestones/${milestoneId}/verifications?limit=10${before ? `&before=${before}` : ""}`,
    JSON.stringify([revision, refresh]),
  );
  const identity = selected ?? history.data?.items[0]?.id;
  const detail = useResource<Verification>(
    identity ? `${path}/verifications/${identity}` : null,
    JSON.stringify([revision, refresh]),
  );
  const value = detail.data;
  const error = history.error || detail.error;
  return (
    <DetailSection title="Verification and review">
      {error && (
        <Button
          variant="outline"
          onClick={() => {
            history.retry();
            detail.retry();
          }}
        >
          Retry verification
        </Button>
      )}
      {!history.data && !error && <p>Loading verification…</p>}
      {history.data?.items.length === 0 && !selected && (
        <p>
          No verification yet. The coordinator can prepare a report task once
          the contributing work is delivered.
        </p>
      )}
      {value && value.milestone_id === milestoneId && (
        <Review
          key={value.id}
          onDirty={onDirty}
          value={value}
          path={path}
          refresh={() => {
            history.retry();
            detail.retry();
          }}
        />
      )}
      {value && value.milestone_id !== milestoneId && (
        <p role="alert">This verification belongs to another milestone.</p>
      )}
      {history.data && history.data.items.length > 0 && (
        <Disclosure summary="Review history">
          {history.data.items.map((item) => (
            <Button
              key={item.id}
              variant="link"
              className="justify-start"
              onClick={() => setParams({ verification: item.id })}
            >
              {milestoneKey} · {statuses[item.status]} ·{" "}
              <Timestamp date={item.updated_at} />
            </Button>
          ))}
          <div className="actions">
            {before && (
              <Button variant="outline" onClick={() => setBefore(null)}>
                Latest reviews
              </Button>
            )}
            {history.data.next_cursor && (
              <Button
                variant="outline"
                onClick={() => setBefore(history.data!.next_cursor)}
              >
                Older reviews
              </Button>
            )}
          </div>
        </Disclosure>
      )}
    </DetailSection>
  );
}
