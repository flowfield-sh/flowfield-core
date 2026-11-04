import { useContext, useEffect } from "react";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import type { AdjacentSnapshot } from "./AdjacentView";
import { ContentStack, DetailHeading, DetailSection } from "./DetailLayout";
import { EntityPane } from "./EntityPane";
import { runtimeTitles } from "./Inspection";
import { resultHref } from "./navigation";
import { DetailHeader } from "./Presentation";
import { ResourceRetry } from "./ResourceRetry";
import { StatusNotice } from "./StatusNotice";
import { useResource } from "./useResource";
import { WorkspaceLink } from "./WorkspaceLink";
import { ResourceTitles } from "./WorkspaceTabs";
import type { WorkspaceBoard } from "./workspace";

export function RuntimeDetails({
  workspaceId,
  resultId,
  board,
  refresh,
  close,
  publishView,
}: {
  workspaceId: string;
  resultId: string;
  board: WorkspaceBoard;
  refresh: unknown;
  close: () => void;
  publishView: (value: AdjacentSnapshot) => void;
}) {
  const location = useLocation();
  const path = `workspaces/${workspaceId}`;
  const result = useResource<components["schemas"]["ResultVersion"]>(
    `${path}/results/${resultId}`,
    refresh,
  );
  const runtime = useResource<components["schemas"]["Inspection"] | null>(
    `${path}/inspection?result_id=${encodeURIComponent(resultId)}`,
    refresh,
  );
  const value = runtime.data;
  const version = result.data;
  const task = board.tasks.find((task) => task.id === version?.task_id);
  const href = resultHref(workspaceId, resultId);
  const setTitle = useContext(ResourceTitles);
  useEffect(() => {
    setTitle?.(
      href + "/runtime",
      `${version?.task_key ? version.task_key + " " : ""}Runtime details`,
    );
    publishView({
      workspaceId,
      pathname: location.pathname,
      context:
        version && !result.error
          ? {
              view: {
                surface: "request",
                request_kind: "result",
                request_id: resultId,
              },
              task: null,
            }
          : null,
    });
  }, [
    setTitle,
    href,
    version,
    result.error,
    resultId,
    workspaceId,
    location.pathname,
    publishView,
  ]);
  return (
    <EntityPane
      label="Runtime details"
      identity={href}
      returnFocusHref={href}
      close={close}
      responseFooter={false}
    >
      <section className="editor request-detail">
        <DetailHeader title="Runtime details" close={close} />
        <ContentStack space="section">
          <ResourceRetry resources={[result, runtime]} />
          {version && (
            <DetailHeading
              title={task?.title ?? `Result ${version.version}`}
              metadata={`${version.task_key} · Result ${version.version}`}
            />
          )}
          {!value && runtime.loading && <p>Loading runtime…</p>}
          {!value && !runtime.loading && !runtime.error && (
            <p>This result has not been prepared.</p>
          )}
          {value && (
            <>
              <StatusNotice
                title={runtimeTitles[value.runtime_state]}
                tone={
                  value.runtime_state === "failed" ||
                  value.runtime_state === "uncertain"
                    ? "attention"
                    : "neutral"
                }
                actions={
                  <Button size="sm" variant="outline" asChild>
                    <WorkspaceLink to={href} intent="open">
                      View result
                    </WorkspaceLink>
                  </Button>
                }
              >
                {value.runtime_problem && <p>{value.runtime_problem}</p>}
                {value.problem && <p>{value.problem}</p>}
              </StatusNotice>
              <DetailSection
                title="Runtime output"
                actions={
                  value.runtime_output ? (
                    <Button size="sm" variant="outline" asChild>
                      <a
                        href={`/api/${path}/inspections/${value.id}/runtime/log`}
                        download
                      >
                        Download log
                      </a>
                    </Button>
                  ) : undefined
                }
              >
                {value.runtime_truncated && (
                  <p>
                    Showing recent output. Download the log for earlier output.
                  </p>
                )}
                {value.runtime_log_truncated && (
                  <p>The retained log reached its size limit.</p>
                )}
                {value.runtime_output ? (
                  <pre
                    className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs"
                    tabIndex={0}
                    aria-label="Runtime output"
                  >
                    {value.runtime_output}
                  </pre>
                ) : (
                  <p>No output yet.</p>
                )}
              </DetailSection>
              <DetailSection title="Saved commands">
                <div>
                  <p>Preparation</p>
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">
                    {value.setup_commands.join("\n") || "No setup commands."}
                  </pre>
                </div>
                <div>
                  <p>Run</p>
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">
                    {value.run_command || "No run command."}
                  </pre>
                </div>
                {value.ready_command && (
                  <div>
                    <p>Readiness</p>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">
                      {value.ready_command}
                    </pre>
                  </div>
                )}
              </DetailSection>
            </>
          )}
        </ContentStack>
      </section>
    </EntityPane>
  );
}
