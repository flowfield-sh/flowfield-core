import { useParams } from "react-router";
import { ExecutionDetails } from "./Runs";
import { ActivityEntry } from "./ActivityEntry";
import { ContentStack } from "./DetailLayout";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";
import { request, type Task, type WorkspaceBoard } from "./workspace";
import type { components } from "./api-schema";
import { MessageBody, merge, hasBody, type Message } from "./TaskMessages";
import { TaskChanges } from "./TaskChanges";
import { RunActivity } from "./RunActivity";
import { ReviewActivity } from "./TaskReview";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { resultHref } from "./navigation";
import { requestHref } from "./requestLinks";
import { ResultFiles } from "./Files";

type Page = components["schemas"]["ThreadPage"];

async function readThread(path: string, signal: AbortSignal): Promise<Page> {
  let page = await request<Page>(path, "GET", undefined, signal);
  const visited = new Set<string>();
  while (page.next_cursor) {
    const cursor = page.next_cursor;
    if (visited.has(cursor))
      throw new Error(
        "Could not advance through task activity. Retry loading it.",
      );
    visited.add(cursor);
    const next = await request<Page>(
      `${path}?cursor=${encodeURIComponent(cursor)}`,
      "GET",
      undefined,
      signal,
    );
    page = { ...next, items: merge(page.items, next.items) };
  }
  return page;
}

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
  const path = `workspaces/${projectId}/tasks/${task.id}`;
  const page = useResource<Page>(
    `${path}/thread`,
    refresh,
    10000,
    undefined,
    readThread,
  );
  const { runId: executionId } = useParams();
  const execution = useResource<components["schemas"]["ExecutionItem"]>(
    executionId ? `${path}/executions/${executionId}` : null,
    refresh,
  );
  const selected = useResource<Message>(
    execution.data ? `${path}/thread/attempt:${execution.data.run_id}` : null,
    refresh,
  );
  const messages = merge(
    page.data?.items ?? [],
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
  const groups = groupMessages(ordered);
  return (
    <ContentStack space="section" className="task-conversation">
      <ResourceRetry resources={[page, execution, selected]} />
      {page.loading && !page.data && <p>Loading activity…</p>}
      <div
        role="list"
        aria-label="Task feed"
        className="content-stack"
        data-space="section"
      >
        {groups.map((group) => (
          <section
            role="listitem"
            key={group.id}
            className="task-feed-cycle rounded-lg border"
            aria-label={group.title}
          >
            <h3 className="task-feed-cycle-title">{group.title}</h3>
            <ol
              className="conversation-messages"
              aria-label={group.title + " events"}
            >
              {group.messages.map((message) => (
                <li
                  key={message.id}
                  data-message-id={message.id}
                  data-kind={message.kind}
                  className="task-activity-item"
                >
                  <ActivityEntry
                    identity={path + ":" + message.id}
                    selected={message.id === selected.data?.id}
                    title={message.title}
                    state={message.state}
                    hasBody={hasBody(message)}
                    metadata={
                      <>
                        {message.actor.label} ·{" "}
                        <Timestamp date={message.created_at} />
                      </>
                    }
                  >
                    <ContentStack className="conversation-message-body">
                      {!["definition", "review"].includes(message.kind) && (
                        <MessageBody message={message} path={path} />
                      )}
                      {message.kind === "question" && (
                        <WorkspaceLink
                          to={requestHref(
                            projectId,
                            "question",
                            message.source_id,
                          )}
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
                          to={requestHref(
                            projectId,
                            "question",
                            message.question_id,
                          )}
                        >
                          Related question
                        </WorkspaceLink>
                      )}
                      {message.kind === "reply" && message.result_id && (
                        <WorkspaceLink
                          to={resultHref(projectId, message.result_id)}
                        >
                          Tested result
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
                      {message.kind === "definition" && (
                        <TaskChanges
                          revision={message.revision!}
                          path={`workspaces/${projectId}/view/tasks/${task.id}`}
                          tasks={board.tasks}
                        />
                      )}
                      {message.kind === "attempt" &&
                        execution.data?.run_id === message.source_id && (
                          <ExecutionDetails
                            projectId={projectId}
                            taskKey={task.key}
                            item={execution.data}
                            refresh={refresh}
                          />
                        )}
                    </ContentStack>
                  </ActivityEntry>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
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
        {result.data?.completion === "code" ? "Access results" : "View result"}
      </WorkspaceLink>
    </ContentStack>
  );
}

/** Derive consecutive work cycles from recorded feedback/reopening, never editable stages. */
function groupMessages(messages: Message[]) {
  const groups: { id: string; title: string; messages: Message[] }[] = [];
  let corrections = 0,
    followUps = 0;
  for (const message of messages) {
    let group = groups.at(-1);
    const boundary = message.cycle_start;
    if (
      !group ||
      (boundary &&
        group.messages.some(
          (item) => item.kind === "attempt" || item.kind === "delivery",
        ))
    ) {
      group = {
        id: message.id,
        title: !group
          ? "Initial work"
          : boundary === "follow_up"
            ? "Follow-up #" + ++followUps
            : "Correction #" + ++corrections,
        messages: [],
      };
      groups.push(group);
    }
    group.messages.push(message);
  }
  return groups;
}
