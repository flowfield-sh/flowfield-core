import { ResourceRetry } from "./ResourceRetry";
import { authorLabel } from "./workspace";
import { UsageSummary } from "./UsageSummary";
import {
  ContentStack,
  DetailHeading,
  DetailSection,
  Disclosure,
} from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { lazy, Suspense, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import type { components } from "./api-schema";
import type { DiffViewState } from "./CodeDiff";
import { WorkspaceLink as Link } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { choiceLabel } from "./HarnessModels";
import { taskHref } from "./navigation";
import { label } from "./workspace";
import { Markdown } from "./Markdown";
import { CheckEvidence, IntegrationEvidence } from "./Integration";

const CodeDiff = lazy(() => import("./CodeDiff"));
type Run = components["schemas"]["Run"];
type Item = components["schemas"]["ExecutionItem"];
const active = ["preparing", "running", "stopping", "uncertain"];

function executionTitle(item: Item, availability = false) {
  if (item.kind === "worker")
    return item.purpose === "Reply to message"
      ? "Reply to message"
      : item.purpose === "Correct result"
        ? "Correct integration"
        : item.purpose === "Revise result"
          ? "Address review feedback"
          : "Implement task";
  const review = item.version == null ? "Review" : `Review ${item.version}`;
  return `${review} ${item.kind === "delivery" ? "delivery" : availability ? "branch checks" : "checks"}`;
}

export function ExecutionDetails({
  projectId,
  taskKey,
  refresh,
  item,
}: {
  projectId: string;
  taskKey: string;
  refresh: unknown;
  item: Item;
}) {
  const [view, setView] = useState<DiffViewState>({
    selected: 0,
    mode: "unified",
  });
  return (
    <section aria-label="Execution details" className="content-stack">
      <ExecutionDetail
        item={item}
        projectId={projectId}
        taskKey={taskKey}
        refresh={refresh}
        view={view}
        changeView={setView}
      />
    </section>
  );
}

function ExecutionDetail({
  item,
  projectId,
  taskKey,
  refresh,
  view,
  changeView,
}: {
  item: Item;
  projectId: string;
  taskKey: string;
  refresh: unknown;
  view: DiffViewState;
  changeView: (view: DiffViewState) => void;
}) {
  const path = `projects/${projectId}`;
  const run = useResource<Run>(
    item.kind === "worker" ? `${path}/runs/${item.run_id}` : null,
    refresh,
  );
  const integration = useResource<components["schemas"]["Integration"]>(
    item.integration_id ? `${path}/integrations/${item.integration_id}` : null,
    refresh,
  );
  const result = useResource<components["schemas"]["ResultVersion"]>(
    item.result_id ? `${path}/results/${item.result_id}` : null,
    refresh,
  );
  return (
    <ContentStack space="section" className="run-detail">
      <DetailHeading
        title={executionTitle(
          item,
          integration.data?.purpose === "availability",
        )}
        status={label(item.status)}
        metadata={
          <>
            {item.owner} · <Timestamp date={item.created_at} />
            {item.ended_at
              ? ` · ${Math.max(0, Math.round((Date.parse(item.ended_at) - Date.parse(item.created_at)) / 1000))}s`
              : ""}
            {run.data && (
              <>
                {" "}
                ·{" "}
                {run.data.applied_agent
                  ? choiceLabel(run.data.applied_agent)
                  : run.data.agent_settings
                    ? choiceLabel(run.data.agent_settings.choice)
                    : [run.data.model, run.data.effort]
                        .filter(Boolean)
                        .join(" · ")}
                {run.data.applied_agent?.mode &&
                  ` · ${run.data.applied_agent.mode}`}
                {run.data.applied_agent?.fast != null &&
                  ` · ${run.data.applied_agent.fast ? "Fast" : "Normal speed"}`}
                {run.data.agent_settings &&
                  ` · ${run.data.agent_settings.source === "override" ? "Task override" : "Project defaults"}`}
              </>
            )}
          </>
        }
      >
        {item.result_id && (
          <Link
            to={`${taskHref(projectId, { key: taskKey }, "result")}/${item.result_id}`}
          >
            Review these changes
          </Link>
        )}
      </DetailHeading>
      <ResourceRetry resources={[run, integration, result]} />
      {item.kind === "worker" && run.data && (
        <WorkerEvidence
          key={run.data.id}
          incoming={run.data}
          view={view}
          changeView={changeView}
          projectId={projectId}
          taskKey={taskKey}
        />
      )}
      {item.kind !== "worker" && (
        <>
          <p>
            {item.kind === "validation"
              ? integration.data?.purpose === "availability"
                ? "Flowfield checks the delivered code on the current branch so dependent work can use it."
                : "Flowfield checks the proposed code combined with the branch. Passing checks still requires your approval before delivery."
              : "Your approval asked Flowfield to deliver these checked changes to the branch."}
          </p>
          {item.kind === "delivery" && result.data && (
            <p>
              Approved by {authorLabel(result.data.approved_by)} ·{" "}
              {result.data.approved_at && (
                <Timestamp date={result.data.approved_at} />
              )}{" "}
              · {label(result.data.status)}
            </p>
          )}
          {result.data?.problem && (
            <Alert variant="destructive">
              <AlertDescription>{result.data.problem}</AlertDescription>
            </Alert>
          )}
          {integration.data && (
            <IntegrationEvidence
              projectId={projectId}
              record={integration.data}
              view={view}
              changeView={changeView}
            />
          )}
        </>
      )}
    </ContentStack>
  );
}

function WorkerEvidence({
  incoming,
  projectId,
  taskKey,
  view,
  changeView,
}: {
  incoming: Run;
  projectId: string;
  taskKey: string;
  view: DiffViewState;
  changeView: (view: DiffViewState) => void;
}) {
  const record = incoming;
  const [inspect, setInspect] = useState(false);
  const path = `projects/${projectId}/runs/${record.id}`;
  const location = useResource<components["schemas"]["RunLocation"]>(
    inspect ? `${path}/location` : null,
    record.revision,
  );
  return (
    <ContentStack space="section">
      {record.problem && (
        <Alert variant="destructive">
          <AlertDescription>{record.problem}</AlertDescription>
        </Alert>
      )}
      {record.correction && (
        <p>
          Correction attempt {record.correction.number} of 2 · both the worker’s
          changes and current branch code are included.
        </p>
      )}
      <CheckEvidence checks={record.setup_checks ?? []} title="Runtime setup" />
      {record.predecessor_id && (
        <Link
          to={`${taskHref(projectId, { key: taskKey }, "runs")}/worker:${record.predecessor_id}`}
        >
          Previous worker attempt
        </Link>
      )}
      {record.feedback && (
        <DetailSection title="Assignment feedback">
          <Markdown>{record.feedback}</Markdown>
        </DetailSection>
      )}
      {record.result ? (
        <>
          <DetailSection title="Worker report">
            <Markdown>{record.result.summary}</Markdown>
          </DetailSection>
          <DetailSection title="Reported checks">
            <Markdown>{record.result.checks}</Markdown>
          </DetailSection>
          {record.result.limitations && (
            <DetailSection title="Limitations">
              <Markdown>{record.result.limitations}</Markdown>
            </DetailSection>
          )}
        </>
      ) : (
        <p>
          {active.includes(record.status)
            ? "The worker has not submitted a result yet."
            : "No result was captured."}
        </p>
      )}
      {record.result_commit && (
        <Suspense fallback={<p>Loading worker changes…</p>}>
          <CodeDiff
            path={path}
            result={record.result_commit}
            view={view}
            changeView={changeView}
          />
        </Suspense>
      )}
      <ContentStack space="flush">
        <Disclosure
          summary={<>Worker workspace</>}
          className="run-inspection"
          onToggle={(e) => setInspect(e.currentTarget.open)}
        >
          {!!record.excluded_files?.length && (
            <Disclosure summary={<>Excluded generated files</>}>
              <p>
                Ignored files remain in this attempt and are excluded from
                delivery.
              </p>
              <pre className="evidence-output">
                {record.excluded_files.join("\n")}
              </pre>
            </Disclosure>
          )}
          <ResourceRetry resources={[location]} />
          {location.data?.workspace && (
            <>
              <DetailSection title="Workspace">
                <code>{location.data.workspace}</code>
                <pre className="evidence-output">
                  {location.data.diff_command}
                </pre>
                <p>
                  These are worker files. Use Try result beside the result to
                  run the combined candidate.
                </p>
              </DetailSection>
            </>
          )}
        </Disclosure>
        <Disclosure summary={<>Token usage</>}>
          <UsageSummary
            usage={record.usage}
            active={active.includes(record.status)}
          />
        </Disclosure>
      </ContentStack>
    </ContentStack>
  );
}
