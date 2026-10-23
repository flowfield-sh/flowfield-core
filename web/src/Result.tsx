import { useLocation } from "react-router";
import { TaskReview } from "./TaskReview";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import {
  ContentStack,
  DetailHeading,
  DetailSection,
  Disclosure,
} from "./DetailLayout";
import { lazy, Suspense, useState, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { components } from "./api-schema";
import type { DiffViewState } from "./CodeDiff";
import { Markdown } from "./Markdown";
import { WorkspaceLink } from "./WorkspaceLink";
import { usePage, useResource } from "./useResource";
import { label, request, type WorkspaceBoard } from "./workspace";
import { taskHref } from "./navigation";
import { FailureEvidence } from "./Integration";
import { ReviewChecks } from "./ReviewChecks";
import { checking, type IntegrationRead } from "./ValidationProgress";
import { Inspection } from "./Inspection";
import { ResultActions, TaskActionContext } from "./TaskActions";
import { ResultFiles } from "./Files";

const CodeDiff = lazy(() => import("./CodeDiff"));
type Version = components["schemas"]["ResultVersion"];

export function Result({
  projectId,
  projects,
  taskId,
  taskKey,
  taskTitle,
  refresh,
  versionId,
  onReply,
  onApprove,
  inputEnabled,
  accepted,
}: {
  projectId: string;
  projects: WorkspaceBoard["projects"];
  taskId: string;
  taskKey: string;
  taskTitle: string;
  refresh: unknown;
  versionId: string;
  onReply: (version: Version) => void;
  onApprove: (version: Version) => void;
  inputEnabled: boolean;
  accepted?: Version | null;
}) {
  const { pathname } = useLocation();
  // Returning to a retained result rechecks external branch movement too.
  const observed = useMemo(() => ({ refresh, pathname }), [refresh, pathname]);
  const path = `workspaces/${projectId}`;
  const page = usePage<components["schemas"]["ResultPage"]>(
    `${path}/tasks/${taskId}/results`,
    observed,
  );
  const detail = useResource<Version>(`${path}/results/${versionId}`, observed);
  const [saved, setSaved] = useState<Version | null>(null);
  const incoming =
    accepted?.id === versionId &&
    accepted.revision > (detail.data?.revision ?? 0)
      ? accepted
      : detail.data;
  const version =
    saved && saved.id === incoming?.id && saved.revision > incoming.revision
      ? saved
      : incoming;
  const [views, setViews] = useState<Record<string, DiffViewState>>({});
  const [diffOpen, setDiffOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const evidence = useResource<IntegrationRead>(
    version?.integration_id
      ? `${path}/integrations/${version.integration_id}`
      : null,
    refresh,
    10000,
    { workspaceId: projectId, attemptId: version?.run_id, isActive: checking },
  );
  const worker = useResource<components["schemas"]["Run"]>(
    version?.run_id ? `${path}/runs/${version.run_id}` : null,
    refresh,
  );
  const availability = useResource<IntegrationRead>(
    version?.availability_id
      ? `${path}/integrations/${version.availability_id}`
      : null,
    refresh,
    10000,
    { workspaceId: projectId, attemptId: version?.run_id, isActive: checking },
  );
  const successor = useResource<components["schemas"]["Run"]>(
    page.data?.current_run_id && page.data.current_run_id !== version?.run_id
      ? `${path}/runs/${page.data.current_run_id}`
      : null,
    refresh,
  );
  const current =
    version?.id === page.data?.current_id &&
    version?.run_id === page.data?.current_run_id;
  const latestResult = page.data?.items.find(
    (item) => item.id === page.data?.current_id,
  );
  const settingsHref =
    version?.next_action?.settings === "reviewer"
      ? `${taskHref(projectId, { key: taskKey })}/settings`
      : version?.next_action?.settings === "integration"
        ? worker.data?.project_id
          ? `/projects/${worker.data.project_id}/edit/integration`
          : null
        : `/workspaces/${projectId}/edit/workers`;
  const href = `/workspaces/${encodeURIComponent(projectId)}/results`;
  async function recover(
    action: "prepare" | "correct" | "cancel" | "revalidate" | "retry-delivery",
  ) {
    if (!version) return;
    setBusy(true);
    try {
      const value = await request<Version>(
        `${path}/results/${version.id}/${action}`,
        "POST",
        {
          expected_revision: version.revision,
          author: "human",
          note: "",
        },
      );
      detail.invalidate();
      page.invalidate();
      worker.invalidate();
      availability.invalidate();
      setSaved(value);
    } catch (e) {
      reportError(e, "Could not update result");
    } finally {
      setBusy(false);
    }
  }
  return (
    <ContentStack
      space="section"
      role="region"
      aria-label="Proposed result"
      className="task-result"
      data-result-id={versionId}
    >
      <ResourceRetry
        resources={[page, detail, evidence, availability, worker, successor]}
      />
      {page.loading && !page.data && <p>Loading result…</p>}
      {page.data && !page.data.items.length && (
        <p className="muted">
          No changes to review yet. The worker’s submission will appear here
          after checking.
        </p>
      )}
      {version && version.task_id !== taskId && (
        <Alert variant="destructive">
          <AlertDescription>
            This result belongs to another task.
          </AlertDescription>
        </Alert>
      )}
      {version && version.task_id === taskId && (
        <>
          <DetailHeading
            title={taskTitle}
            metadata={
              version.completion === "code" && version.target_branch ? (
                <>
                  Project:{" "}
                  {worker.data?.project_id ? (
                    <WorkspaceLink
                      className="text-inherit"
                      to={`/projects/${worker.data.project_id}/overview`}
                    >
                      {projects.find(
                        (project) => project.id === worker.data?.project_id,
                      )?.name ?? worker.data.project_id}
                    </WorkspaceLink>
                  ) : (
                    "…"
                  )}
                  {" · Branch: "}
                  {version.target_branch}
                </>
              ) : undefined
            }
          />
          {page.data && !current && (
            <p className="detail-metadata">
              {page.data.current_id && page.data.current_id !== version.id ? (
                <>
                  {latestResult
                    ? `Result #${latestResult.version} is ${label(latestResult.status).toLowerCase()}. `
                    : "A newer result is available. "}
                  <WorkspaceLink to={`${href}/${page.data.current_id}`}>
                    View current result
                  </WorkspaceLink>
                </>
              ) : (
                <>
                  A newer worker attempt is{" "}
                  {label(successor.data?.status ?? "pending")}.
                </>
              )}
            </p>
          )}
          {current && ["preparing", "delivering"].includes(version.status) && (
            <p role="status">
              {version.status === "preparing"
                ? "Preparing and checking changes…"
                : "Integrating changes…"}
            </p>
          )}
          {current && version.status === "changes_requested" && (
            <p role="status">{version.next_action?.reason}</p>
          )}
          {version.completion === "code" && version.candidate_commit && (
            <DetailSection title="Run result">
              <Inspection
                key={version.id}
                projectId={projectId}
                result={version}
                refresh={refresh}
              />
            </DetailSection>
          )}
          <DetailSection title="Outcome">
            <Markdown>{version.report.summary}</Markdown>
            {version.report.outcome === "partial" && (
              <DetailSection title="Work remaining">
                <Markdown>{version.report.remaining_work}</Markdown>
              </DetailSection>
            )}

            {version.report.limitations && (
              <DetailSection title="Limitations">
                <Markdown>{version.report.limitations}</Markdown>
              </DetailSection>
            )}
            {version.feedback && (
              <DetailSection title="Review request">
                <Markdown>{version.feedback}</Markdown>
              </DetailSection>
            )}
          </DetailSection>
          <ResultFiles
            files={version.files}
            supporting={version.completion === "code"}
          />
          {current && (
            <TaskActionContext>
              <TaskReview
                projectId={projectId}
                taskId={taskId}
                taskKey={taskKey}
                refresh={refresh}
              />
            </TaskActionContext>
          )}
          {version.independent_review?.summary && (
            <DetailSection title="Reviewer">
              {version.independent_review.status === "stale" && (
                <p>
                  This assessment is outdated. Changed inputs need a fresh
                  review.
                </p>
              )}
              <p>{version.independent_review.summary}</p>
              <Disclosure summary="Review evidence">
                <Markdown>{version.independent_review.evidence}</Markdown>
              </Disclosure>
            </DetailSection>
          )}
          {current && version.status === "preparing" && (
            <ResultActions version={version.version}>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void recover("cancel")}
              >
                Stop checks
              </Button>
            </ResultActions>
          )}
          {current && version.status === "cancelled" && (
            <p>Checks stopped. Code and output were kept.</p>
          )}
          {current &&
            version.problem &&
            version.problem_code !== "partial_outcome" &&
            version.status !== "cancelled" && (
              <FailureEvidence problem={version.problem} />
            )}
          {current &&
            version.next_action &&
            !["none", "wait", "review_result"].includes(
              version.next_action.action,
            ) && (
              <ResultActions
                version={version.version}
                context={
                  <DetailSection
                    title="Next action"
                    aria-label="Result recovery"
                  >
                    <p>{version.next_action.reason}</p>
                    {version.next_action.action === "coordinator" && (
                      <p>
                        Ask the coordinator about{" "}
                        <WorkspaceLink
                          to={taskHref(projectId, { key: taskKey })}
                        >
                          {taskKey}
                        </WorkspaceLink>
                        .
                      </p>
                    )}
                    {settingsHref &&
                      version.next_action.settings &&
                      version.next_action.action !== "settings" && (
                        <WorkspaceLink to={settingsHref}>
                          Project setup
                        </WorkspaceLink>
                      )}
                  </DetailSection>
                }
              >
                {version.next_action.action === "settings" ? (
                  settingsHref ? (
                    <Button size="sm" asChild>
                      <WorkspaceLink to={settingsHref}>
                        {version.next_action.label}
                      </WorkspaceLink>
                    </Button>
                  ) : (
                    <Button size="sm" disabled>
                      {version.next_action.label}
                    </Button>
                  )
                ) : version.next_action.action === "coordinator" ? null : (
                  <Button
                    type="button"
                    size="sm"
                    disabled={busy || !inputEnabled}
                    onClick={() => {
                      const action = version.next_action!.action;
                      if (action === "request_changes") onReply(version);
                      else if (
                        action === "prepare" ||
                        action === "correct" ||
                        action === "revalidate" ||
                        action === "retry-delivery"
                      )
                        void recover(action);
                    }}
                  >
                    {version.next_action.label}
                  </Button>
                )}
              </ResultActions>
            )}
          {
            <DetailSection title="Checks" role="region" aria-label="Checks">
              {version.reuse_action && (
                <div className="space-y-2">
                  <p>
                    {version.reuse_action.action === "revalidate"
                      ? version.reuse_action.reason
                      : version.reuse_action.label}
                  </p>
                  {version.reuse_action.action === "revalidate" && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy || !inputEnabled}
                      onClick={() => void recover("revalidate")}
                    >
                      {busy
                        ? "Checking destination…"
                        : version.reuse_action.label}
                    </Button>
                  )}
                </div>
              )}
              {version.integration_id && !evidence.data ? (
                <p>
                  {evidence.loading ? "Loading checks…" : "Checks unavailable."}
                </p>
              ) : (
                <ReviewChecks
                  preparation={evidence.data}
                  availability={availability.data}
                  reusePending={version.reuse_action?.action === "revalidate"}
                  report={version.report.checks}
                  completion={version.completion}
                  branch={version.target_branch}
                  historyHref={taskHref(projectId, { key: taskKey }, "history")}
                />
              )}
            </DetailSection>
          }
          {version.completion === "code" && version.candidate_commit && (
            <DetailSection title="Details">
              {version.integration_id && version.candidate_commit && (
                <Disclosure
                  summary={<>Code changes</>}
                  onToggle={(e) => setDiffOpen(e.currentTarget.open)}
                >
                  {diffOpen && (
                    <Suspense fallback={<p>Loading changes…</p>}>
                      <CodeDiff
                        path={`${path}/integrations/${version.integration_id}`}
                        result={version.candidate_commit}
                        view={
                          views[version.id] ?? {
                            selected: 0,
                            mode: "unified",
                          }
                        }
                        changeView={(view) =>
                          setViews((current) => ({
                            ...current,
                            [version.id]: view,
                          }))
                        }
                      />
                    </Suspense>
                  )}
                </Disclosure>
              )}
            </DetailSection>
          )}
          {current &&
            version.completion === "code" &&
            version.next_action?.action === "review_result" &&
            version.status === "ready" && (
              <ResultActions version={version.version}>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy || !inputEnabled}
                  onClick={() => onApprove(version)}
                >
                  Approve and integrate
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !inputEnabled}
                  onClick={() => onReply(version)}
                >
                  Request changes
                </Button>
              </ResultActions>
            )}
        </>
      )}
    </ContentStack>
  );
}
