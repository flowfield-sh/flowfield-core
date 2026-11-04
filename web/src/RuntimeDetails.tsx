import { useContext, useEffect } from "react";
import { useLocation } from "react-router";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { components } from "./api-schema";
import type { AdjacentSnapshot } from "./AdjacentView";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { EntityPane } from "./EntityPane";
import { runtimeTitles } from "./Inspection";
import { resultHref } from "./navigation";
import { DetailHeader } from "./Presentation";
import { ResourceRetry } from "./ResourceRetry";
import { useActivityFollow } from "./useActivityFollow";
import { useResource } from "./useResource";
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
  const { pane: outputRef, content: contentRef } = useActivityFollow(
    true,
    !!value,
  );
  const version = result.data;
  const task = board.tasks.find((task) => task.id === version?.task_id);
  const href = resultHref(workspaceId, resultId);
  const setTitle = useContext(ResourceTitles);
  useEffect(() => {
    setTitle?.(
      href + "/runtime",
      `${version?.task_key ? version.task_key + " " : ""}Result output`,
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
      label="Result output"
      identity={href}
      returnFocusHref={href}
      close={close}
      responseFooter={false}
    >
      <section className="editor request-detail">
        <DetailHeader
          title={
            <span className="feed-event-title">
              {version && <Badge variant="outline">{version.task_key}</Badge>}
              <span>Result output</span>
            </span>
          }
          close={close}
        />
        <ContentStack space="section">
          <ResourceRetry resources={[result, runtime]} />
          {version && (
            <DetailHeading
              title={task?.title ?? `Result ${version.version}`}
              metadata={value ? runtimeTitles[value.runtime_state] : undefined}
            />
          )}
          {!value && runtime.loading && <p>Loading runtime…</p>}
          {!value && !runtime.loading && !runtime.error && (
            <p>This result has not been prepared.</p>
          )}
          {value && (
            <>
              <div className="actions">
                {value.runtime_output && (
                  <Button size="sm" variant="outline" asChild>
                    <a
                      href={`/api/${path}/inspections/${value.id}/runtime/log`}
                      download
                    >
                      Download log
                    </a>
                  </Button>
                )}
              </div>
              <div
                ref={outputRef}
                className="run-activity-output"
                role="region"
                aria-label="Runtime output"
                tabIndex={0}
              >
                <div ref={contentRef}>
                  <pre className="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed">
                    {value.runtime_output || "No output yet."}
                  </pre>
                </div>
              </div>
              {value.runtime_truncated && (
                <p className="muted">
                  Showing recent output. Download the log for earlier output.
                </p>
              )}
              {value.runtime_log_truncated && (
                <p className="muted">
                  The retained log reached its size limit.
                </p>
              )}
            </>
          )}
        </ContentStack>
      </section>
    </EntityPane>
  );
}
