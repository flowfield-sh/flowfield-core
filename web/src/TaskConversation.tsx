import { useRef, useState } from "react";
import { useParams } from "react-router";
import { ActivityEntry } from "./ActivityEntry";
import { ContentStack } from "./DetailLayout";
import { LoadMore } from "./LoadMore";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { useResource } from "./useResource";
import { useFeedScroll } from "./useFeedScroll";
import { request, type Task, type WorkspaceBoard } from "./workspace";
import type { components } from "./api-schema";
import { MessageBody, merge, hasBody, type Message } from "./TaskMessages";
import { TaskChanges } from "./TaskChanges";
import { RunActivity } from "./RunActivity";
import { ReviewActivity } from "./TaskReview";
import { Timestamp, ExactTimeTooltip } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref, resultHref } from "./navigation";
import { requestHref } from "./requestLinks";
import { ResultFiles } from "./Files";
import { ExecutionDetails } from "./Runs";

type Page = components["schemas"]["ThreadPage"];

/** Tasks retain the chronology. Exact requests/results own every response. */
export function TaskConversation({
  projectId,
  task,
  board,
  refresh,
}: {
  projectId: string;
  task: Task;
  board: Pick<WorkspaceBoard, "tasks">;
  refresh: unknown;
}) {
  const route = useParams();
  const path = `workspaces/${projectId}/tasks/${task.id}`;
  const page = useResource<Page>(`${path}/thread`, refresh);
  const initial = useResource<Message>(`${path}/thread/definition:1`, task.id);
  const executionId = route.tab === "history" ? route.runId : undefined;
  const execution = useResource<components["schemas"]["ExecutionItem"]>(
    executionId ? `${path}/executions/${executionId}` : null,
    refresh,
  );
  const target =
    route.tab === "conversation"
      ? route.runId
      : execution.data
        ? `attempt:${execution.data.run_id}`
        : undefined;
  const selected = useResource<Message>(
    target ? `${path}/thread/${encodeURIComponent(target)}` : null,
    refresh,
  );
  const [items, setItems] = useState<Message[]>([]);
  const [seen, setSeen] = useState<Page | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  if (page.data && page.data !== seen) {
    setSeen(page.data);
    setItems((values) => merge(values, page.data!.items));
    if (!seen) setCursor(page.data.next_cursor);
  }
  const messages = merge(
    merge(items, initial.data ? [initial.data] : []),
    selected.data ? [selected.data] : [],
  );
  const active = [...messages]
    .reverse()
    .find(
      (m) =>
        ["attempt", "review"].includes(m.kind) &&
        ["preparing", "running", "stopping"].includes(m.status ?? ""),
    );
  const ordered = active
    ? [...messages.filter((m) => m !== active), active]
    : messages;
  const root = useRef<HTMLDivElement>(null);
  const scrolling = useFeedScroll(
    root,
    !!page.data && !!initial.data && (!executionId || !!execution.data),
    selected.data?.id ?? target,
    !!selected.data,
  );
  const [busy, setBusy] = useState(false);
  async function older() {
    if (!cursor) return;
    setBusy(true);
    try {
      const next = await request<Page>(
        `${path}/thread?cursor=${encodeURIComponent(cursor)}`,
      );
      scrolling.readingEarlier();
      setItems((values) => merge(values, next.items));
      setCursor(next.next_cursor);
    } catch (error) {
      reportError(error, "Could not load earlier activity");
    } finally {
      setBusy(false);
    }
  }
  return (
    <ContentStack ref={root} space="section" className="task-conversation">
      <ResourceRetry resources={[page, initial, selected, execution]} />
      {page.loading && !page.data && <p>Loading activity…</p>}
      <ol className="conversation-messages" aria-label="Task feed">
        {cursor && (
          <li>
            <LoadMore disabled={busy} onClick={() => void older()}>
              Load earlier activity
            </LoadMore>
          </li>
        )}
        {ordered.map((message) => (
          <li
            key={message.id}
            data-message-id={message.id}
            data-kind={message.kind}
            className="task-activity-item"
          >
            <ActivityEntry
              identity={path + ":" + message.id}
              selected={message.id === (target ?? active?.id)}
              alwaysOpen={message.kind === "answer" || message.kind === "reply"}
              title={message.title}
              state={message.state}
              hasBody={hasBody(message)}
              metadata={
                <>
                  {message.actor.label} ·{" "}
                  <ExactTimeTooltip date={message.created_at}>
                    <WorkspaceLink
                      to={`${taskHref(projectId, task)}/conversation/${encodeURIComponent(message.id)}`}
                      aria-label={"Permalink: " + message.title}
                    >
                      <Timestamp date={message.created_at} tooltip={false} />
                    </WorkspaceLink>
                  </ExactTimeTooltip>
                </>
              }
            >
              <ContentStack className="conversation-message-body">
                {!["definition", "review"].includes(message.kind) && (
                  <MessageBody message={message} path={path} />
                )}
                {message.kind === "question" && (
                  <WorkspaceLink
                    to={requestHref(projectId, "question", message.source_id)}
                  >
                    View request
                  </WorkspaceLink>
                )}
                {message.kind === "result" && (
                  <ResultEvidence
                    workspaceId={projectId}
                    identity={message.source_id}
                    refresh={refresh}
                  />
                )}
                {message.question_id && (
                  <WorkspaceLink
                    to={requestHref(projectId, "question", message.question_id)}
                  >
                    Related question
                  </WorkspaceLink>
                )}
                {message.kind === "reply" && message.result_id && (
                  <WorkspaceLink to={resultHref(projectId, message.result_id)}>
                    Tested result
                  </WorkspaceLink>
                )}
                {message.earlier_id && (
                  <WorkspaceLink
                    to={`${taskHref(projectId, task)}/conversation/activity:${message.earlier_id}`}
                  >
                    Earlier {message.title.toLowerCase()}
                  </WorkspaceLink>
                )}
                {message.successor_id && (
                  <WorkspaceLink
                    to={`${taskHref(projectId, task)}/conversation/activity:${message.successor_id}`}
                  >
                    Replacement {message.title.toLowerCase()}
                  </WorkspaceLink>
                )}
                {message.kind === "attempt" && (
                  <RunActivity
                    projectId={projectId}
                    runId={message.source_id}
                  />
                )}
                {message.kind === "review" && (
                  <ReviewActivity
                    projectId={projectId}
                    reviewId={message.source_id}
                    refresh={refresh}
                  />
                )}
                {message.kind === "attempt" &&
                  executionId &&
                  execution.data?.run_id === message.source_id && (
                    <ExecutionDetails
                      key={executionId}
                      projectId={projectId}
                      taskKey={task.key}
                      item={execution.data}
                      refresh={refresh}
                    />
                  )}
                {message.kind === "definition" && (
                  <TaskChanges
                    revision={message.revision!}
                    path={`workspaces/${projectId}/view/tasks/${task.id}`}
                    tasks={board.tasks}
                  />
                )}
              </ContentStack>
            </ActivityEntry>
          </li>
        ))}
      </ol>
    </ContentStack>
  );
}

function ResultEvidence({
  workspaceId,
  identity,
  refresh,
}: {
  workspaceId: string;
  identity: string;
  refresh: unknown;
}) {
  const result = useResource<components["schemas"]["ResultVersion"]>(
    `workspaces/${workspaceId}/results/${identity}`,
    refresh,
  );
  return (
    <ContentStack>
      <ResourceRetry resources={[result]} />
      {result.data?.independent_review?.summary && (
        <p>Reviewer: {result.data.independent_review.summary}</p>
      )}
      {result.data && (
        <ResultFiles
          files={result.data.files}
          supporting={result.data.completion === "code"}
        />
      )}
      <WorkspaceLink to={resultHref(workspaceId, identity)}>
        {result.data?.completion === "code"
          ? "Run result and view changes"
          : "View result"}
      </WorkspaceLink>
    </ContentStack>
  );
}
