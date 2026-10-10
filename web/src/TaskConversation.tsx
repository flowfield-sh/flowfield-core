import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  Fragment,
  type ReactNode,
} from "react";
import { useParams } from "react-router";
import { Badge } from "@/components/ui/badge";
import { OverlayFooter, useOverlayBody } from "./EntityOverlay";
import { Button } from "@/components/ui/button";
import { Composer } from "./Composer";
import { AgentSettingsControl } from "./AgentSettings";
import { choiceLabel } from "./HarnessModels";
import { ArrowUp } from "lucide-react";
import { ContentStack, DetailHeading } from "./DetailLayout";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request, type Task, type Board } from "./workspace";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
import { Markdown } from "./Markdown";
import { Timestamp, ExactTimeTooltip } from "./Timestamp";
import { Result } from "./Result";
import { ExecutionDetails } from "./Runs";
import { TaskChanges } from "./TaskChanges";
import { RunActivity } from "./RunActivity";
import { useFeedScroll } from "./useFeedScroll";
import { TaskActionHost, WorkerActions } from "./TaskActions";
import { WorkState } from "./WorkState";
import { StageSequence } from "./StageSequence";

type Gate = components["schemas"]["InputEligibility"];
type Binding = components["schemas"]["ReplyCreate"]["binding"];
type Message = components["schemas"]["ThreadMessage"];
type Page = components["schemas"]["ThreadPage"];
type Version = components["schemas"]["ResultVersion"];
type Draft = {
  id: string;
  body: string;
  binding: Binding;
  action: "answer" | "changes" | "approve";
  candidate?: string;
};
const binding = (gate: Gate): Binding => ({
  task_revision: gate.task_revision,
  agreement_revision: gate.agreement_revision,
  question_id: gate.question_id,
  question_revision: gate.question_revision,
  result_id: gate.result_id,
  result_revision: gate.result_revision,
});
const contextKey = (value: Binding) =>
  value.question_id
    ? `question:${value.question_id}`
    : value.result_id
      ? `result:${value.result_id}`
      : "task";
const messageHref = (projectId: string, task: Task, id: string) =>
  `${taskHref(projectId, task)}/conversation/${encodeURIComponent(id)}`;

export function StageHeader({
  projectId,
  task,
  refresh,
}: {
  projectId: string;
  task: Task;
  refresh: unknown;
}) {
  const plan = useResource<components["schemas"]["StagePlan"]>(
    `projects/${projectId}/tasks/${task.id}/stages`,
    refresh,
  );
  return (
    <>
      <ResourceRetry resources={[plan]} />
      {!!plan.data?.stages.length && (
        <div>
          <StageSequence stages={plan.data.stages} label="Task stages" />
          {plan.data.agreement_revision !== task.agreement_revision && (
            <p className="muted">Update stages to match the task.</p>
          )}
        </div>
      )}
    </>
  );
}

export function TaskConversation({
  projectId,
  task,
  board,
  refresh,
  onDirty,
  taskActions,
  onSettingsDirty,
}: {
  projectId: string;
  task: Task;
  board: Board;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
  taskActions: ReactNode;
  onSettingsDirty: (dirty: boolean) => void;
}) {
  const route = useParams();
  const path = `projects/${projectId}/tasks/${task.id}`;
  const [revision, setRevision] = useState(0);
  const tick = useMemo(() => ({ refresh, revision }), [refresh, revision]);
  const page = useResource<Page>(`${path}/thread`, tick);
  const gate = useResource<Gate>(`${path}/input-eligibility`, tick);
  const currentAttempt = useResource<Message>(
    gate.data?.run_id ? `${path}/thread/attempt:${gate.data.run_id}` : null,
    tick,
  );
  const currentResult = useResource<Message>(
    gate.data?.result_id
      ? `${path}/thread/result:${gate.data.result_id}`
      : null,
    tick,
  );
  const currentQuestion = useResource<components["schemas"]["Question"]>(
    gate.data?.question_id
      ? "projects/" + projectId + "/questions/" + gate.data.question_id
      : null,
    tick,
  );
  const initial = useResource<Message>(`${path}/thread/definition:1`, task.id);
  const executionId =
    ["history", "runs"].includes(route.tab ?? "") && route.runId
      ? route.integrationId
        ? "validation:" + route.integrationId
        : route.runId.includes(":")
          ? route.runId
          : "worker:" + route.runId
      : null;
  const execution = useResource<components["schemas"]["ExecutionItem"]>(
    executionId ? `${path}/executions/${executionId}` : null,
    tick,
  );
  const target =
    route.tab === "conversation"
      ? route.runId
      : ["result", "changes"].includes(route.tab ?? "") && route.runId
        ? `result:${route.runId}`
        : execution.data
          ? `attempt:${execution.data.run_id}`
          : undefined;
  const selected = useResource<Message>(
    target ? `${path}/thread/${encodeURIComponent(target)}` : null,
    tick,
  );
  const [items, setItems] = useState<Message[]>([]);
  const [seen, setSeen] = useState<Page | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  if (page.data && page.data !== seen) {
    setSeen(page.data);
    setItems((current) => merge(current, page.data!.items));
    if (!seen) setCursor(page.data.next_cursor);
  }
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [chosen, setChosen] = useState<string | null>(null);
  const currentBinding = gate.data ? binding(gate.data) : null;
  const key = chosen ?? (currentBinding ? contextKey(currentBinding) : "task");
  const draft = drafts[key];
  const inputEnabled =
    !!gate.data?.enabled && (gate.data.reason !== "answer_editable" || !!draft);
  // A selected draft stays open even when empty; eligibility controls delivery.
  const composerAction =
    draft?.action ??
    (gate.data?.question_id && gate.data.reason !== "answer_editable"
      ? "answer"
      : null);
  const showComposer = composerAction !== null;
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [choice, setChoice] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const settingsChanged = useCallback(
    (value: boolean) => {
      setSettingsDirty(value);
      onSettingsDirty(value);
    },
    [onSettingsDirty],
  );
  const settingsControl = (
    <AgentSettingsControl
      projectId={projectId}
      path={`${path}/agent-settings`}
      refresh={refresh}
      open={modelOpen}
      onOpenChange={setModelOpen}
      onDirty={settingsChanged}
      onReady={setChoice}
      label={choice ? choiceLabel(choice) : "Worker settings"}
    />
  );
  const [olderBusy, setOlderBusy] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [seenResult, setSeenResult] = useState<string | null>(null);
  if (gate.data?.result_id && gate.data.result_id !== seenResult) {
    if (seenResult)
      setExpanded((values) => ({ ...values, ["result:" + seenResult]: true }));
    setSeenResult(gate.data.result_id);
  }
  const root = useRef<HTMLDivElement>(null);
  const overlayBody = useOverlayBody();
  const input = useRef<HTMLTextAreaElement>(null);
  const [actionHost, setActionHost] = useState<HTMLDivElement | null>(null);
  const [actionContext, setActionContext] = useState<HTMLDivElement | null>(
    null,
  );
  useEffect(() => {
    const focused = document.activeElement;
    const initialFocus =
      focused === document.body ||
      focused?.classList.contains("entity-overlay-body");
    // Late eligibility reads must not steal focus from evidence or other controls.
    // Choosing a reply/draft explicitly still moves into its composer.
    if (composerAction && inputEnabled && (chosen || initialFocus))
      input.current?.focus({ preventScroll: true });
  }, [composerAction, inputEnabled, key, chosen]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    onDirty(Object.values(drafts).some((d) => !!d.body));
    return () => onDirty(false);
  }, [drafts, onDirty]);
  const chronological = merge(
    merge(
      merge(
        merge(items, currentAttempt.data ? [currentAttempt.data] : []),
        initial.data ? [initial.data] : [],
      ),
      selected.data ? [selected.data] : [],
    ),
    currentResult.data ? [currentResult.data] : [],
  );
  const activeAttempt = chronological.find(
    (message) =>
      message.kind === "attempt" &&
      message.source_id === gate.data?.run_id &&
      ["preparing", "running", "stopping", "uncertain"].includes(
        message.status ?? "",
      ),
  );
  const messages = activeAttempt
    ? [
        ...chronological.filter((message) => message !== activeAttempt),
        activeAttempt,
      ]
    : chronological;
  const replyBinding = draft?.binding ?? currentBinding;
  const replyResult = messages.find(
    (m) => m.kind === "result" && m.source_id === replyBinding?.result_id,
  );
  const replyQuestion = messages.find(
    (m) =>
      m.kind === "question" &&
      m.source_id === replyBinding?.question_id &&
      m.revision === replyBinding?.question_revision,
  );
  const questionText =
    replyQuestion?.question ??
    (replyBinding?.question_id === currentQuestion.data?.id
      ? currentQuestion.data?.question
      : undefined);
  // Current-result links open the live end; explicit message permalinks and
  // older results retain their exact historical anchor.
  const scrollTarget =
    route.tab !== "conversation" &&
    selected.data?.kind === "result" &&
    selected.data.source_id === gate.data?.result_id
      ? undefined
      : (selected.data?.id ?? target);
  const scrolling = useFeedScroll(
    root,
    !!page.data &&
      !!initial.data &&
      !!gate.data &&
      (!executionId || !!execution.data),
    scrollTarget,
    !!selected.data,
  );
  const stale =
    !!draft &&
    !!currentBinding &&
    JSON.stringify(draft.binding) !== JSON.stringify(currentBinding);
  function updateBody(body: string) {
    if (!currentBinding || !composerAction) return;
    setDrafts((values) => ({
      ...values,
      [key]: {
        ...(values[key] ?? {
          id: crypto.randomUUID(),
          binding: currentBinding,
          action: composerAction!,
        }),
        body,
      },
    }));
  }
  function beginDraft(
    action: Draft["action"],
    target: Draft["binding"],
    body = "",
  ) {
    const nextKey = contextKey(target);
    setChosen(nextKey);
    setDrafts((values) => ({
      ...values,
      [nextKey]: {
        ...(values[nextKey] ?? { binding: target, body }),
        id: crypto.randomUUID(),
        action,
      },
    }));
  }
  function discardDraft() {
    setDrafts((values) => {
      const next = { ...values };
      delete next[key];
      return next;
    });
    setChosen(null);
  }
  function requestChanges(version: Version) {
    if (!currentBinding) return;
    beginDraft("changes", {
      ...currentBinding,
      result_id: version.id,
      result_revision: version.revision,
    });
  }
  function approve(version: Version) {
    if (!currentBinding) return;
    const target = {
      ...currentBinding,
      result_id: version.id,
      result_revision: version.revision,
    };
    const nextKey = contextKey(target);
    setChosen(nextKey);
    setDrafts((values) => ({
      ...values,
      [nextKey]: {
        id: crypto.randomUUID(),
        body: "",
        binding: target,
        action: "approve",
        candidate: version.candidate_commit ?? version.source_commit,
      },
    }));
  }
  async function send() {
    if (
      busy ||
      uploading ||
      settingsDirty ||
      !draft ||
      (draft.action !== "approve" && !draft.body.trim()) ||
      !gate.data?.enabled ||
      stale
    )
      return;
    setBusy(true);
    try {
      if (draft.action === "approve") {
        await request(
          `projects/${projectId}/results/${draft.binding.result_id}/review`,
          "POST",
          {
            expected_revision: draft.binding.result_revision,
            candidate_commit: draft.candidate,
            action: "approve",
            note: draft.body,
            author: "human",
          },
        );
      } else await request(`${path}/replies`, "POST", draft);
      if (!alive.current) return;
      page.invalidate();
      gate.invalidate();
      setDrafts((values) => {
        const next = { ...values };
        delete next[key];
        return next;
      });
      setChosen(null);
      setRevision((n) => n + 1);
    } catch (e) {
      if (alive.current) reportError(e, "Could not submit reply");
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function older() {
    if (!cursor) return;
    setOlderBusy(true);
    try {
      const next = await request<Page>(
        `${path}/thread?cursor=${encodeURIComponent(cursor)}`,
      );
      if (!alive.current) return;
      scrolling.readingEarlier();
      setItems((values) => merge(values, next.items));
      setCursor(next.next_cursor);
    } catch (e) {
      if (alive.current) reportError(e, "Could not load earlier activity");
    } finally {
      if (alive.current) setOlderBusy(false);
    }
  }
  async function cancelPending() {
    try {
      await request(
        `${path}/replies/${gate.data!.pending_reply_id}/cancel`,
        "POST",
      );
      setRevision((n) => n + 1);
    } catch (e) {
      reportError(e, "Could not cancel reply");
    }
  }
  return (
    <TaskActionHost.Provider
      value={{ actions: actionHost, context: actionContext }}
    >
      <ContentStack ref={root} space="section" className="task-conversation">
        <ContentStack>
          <strong>Activity</strong>
          <ResourceRetry resources={[page, selected, gate, execution]} />
          {page.loading && !page.data && <p>Loading activity…</p>}
          <ol className="conversation-messages" aria-label="Task feed">
            {messages.map((message, index) => (
              <Fragment key={message.id}>
                {index === 1 && cursor && (
                  <li>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={olderBusy}
                      onClick={() => void older()}
                    >
                      Load earlier activity
                    </Button>
                  </li>
                )}
                <li
                  data-message-id={message.id}
                  data-kind={message.kind}
                  className="conversation-message"
                >
                  <DetailHeading
                    entry
                    inlineMetadata
                    title={
                      <span className="feed-event-title">
                        <WorkState state={message.state} indicatorOnly />
                        <span>
                          {message.kind === "state" &&
                          message.title.startsWith("Moved to ") ? (
                            <>
                              Moved to{" "}
                              <Badge variant="outline">
                                {message.title.slice(9)}
                              </Badge>
                            </>
                          ) : (
                            message.title
                          )}
                        </span>
                      </span>
                    }
                    metadata={
                      <>
                        {message.actor.label} ·{" "}
                        <ExactTimeTooltip date={message.created_at}>
                          <WorkspaceLink
                            to={messageHref(projectId, task, message.id)}
                            aria-label={`Permalink: ${message.title}`}
                          >
                            <Timestamp
                              date={message.created_at}
                              tooltip={false}
                            />
                          </WorkspaceLink>
                        </ExactTimeTooltip>
                      </>
                    }
                  />
                  {hasBody(message) && (
                    <div className="conversation-message-body content-stack">
                      {message.kind === "result" &&
                      (message.source_id === gate.data?.result_id ||
                        expanded[message.id] ||
                        target === message.id) ? (
                        <Result
                          projectId={projectId}
                          taskId={task.id}
                          taskKey={task.key}
                          versionId={message.source_id}
                          refresh={tick}
                          onReply={requestChanges}
                          onApprove={approve}
                          inputEnabled={
                            !!gate.data?.enabled && !gate.data.question_id
                          }
                        />
                      ) : (
                        <>
                          {message.kind !== "definition" && (
                            <MessageBody message={message} path={path} />
                          )}
                          {message.kind === "result" && (
                            <Button size="sm" variant="outline" asChild>
                              <WorkspaceLink
                                onClick={() =>
                                  setExpanded((values) => ({
                                    ...values,
                                    [message.id]: true,
                                  }))
                                }
                                to={messageHref(projectId, task, message.id)}
                              >
                                Inspect earlier result
                              </WorkspaceLink>
                            </Button>
                          )}
                        </>
                      )}
                      {message.question_id && (
                        <WorkspaceLink
                          to={messageHref(
                            projectId,
                            task,
                            `question:${message.question_id}:1`,
                          )}
                        >
                          Related question
                        </WorkspaceLink>
                      )}
                      {message.kind === "reply" && message.result_id && (
                        <WorkspaceLink
                          to={messageHref(
                            projectId,
                            task,
                            `result:${message.result_id}`,
                          )}
                        >
                          Tested result
                        </WorkspaceLink>
                      )}
                      {message.earlier_id && (
                        <WorkspaceLink
                          to={messageHref(
                            projectId,
                            task,
                            `activity:${message.earlier_id}`,
                          )}
                        >
                          Earlier {message.title.toLowerCase()}
                        </WorkspaceLink>
                      )}
                      {message.successor_id && (
                        <WorkspaceLink
                          to={messageHref(
                            projectId,
                            task,
                            `activity:${message.successor_id}`,
                          )}
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
                      {message.kind === "attempt" &&
                        executionId &&
                        execution.data?.run_id === message.source_id && (
                          <ExecutionDetails
                            key={executionId}
                            projectId={projectId}
                            taskKey={task.key}
                            item={execution.data}
                            refresh={tick}
                          />
                        )}
                      {message.kind === "definition" && (
                        <TaskChanges
                          revision={message.revision!}
                          path={`projects/${projectId}/view/tasks/${task.id}`}
                          milestones={board.milestones}
                          tasks={board.tasks}
                        />
                      )}
                    </div>
                  )}
                </li>
              </Fragment>
            ))}
          </ol>
        </ContentStack>
        <OverlayFooter>
          <form
            className="task-input content-stack"
            aria-label="Task input"
            onKeyDown={(event) => {
              if (
                event.key !== "Escape" ||
                event.defaultPrevented ||
                modelOpen ||
                !showComposer
              )
                return;
              event.preventDefault();
              event.stopPropagation();
              if (busy || uploading) return;
              discardDraft();
              requestAnimationFrame(() =>
                overlayBody?.focus({ preventScroll: true }),
              );
            }}
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            {(showComposer || replyResult?.status === "ready") && (
              <div className="task-input-context">
                <strong>
                  {replyBinding?.question_id
                    ? "Answer the worker"
                    : draft?.action === "changes"
                      ? "Request changes"
                      : draft?.action === "approve"
                        ? "Approve and integrate"
                        : replyResult
                          ? (replyResult.status === "ready"
                              ? "Review "
                              : "Message about ") + replyResult.title
                          : "Task response"}
                </strong>
                {replyBinding?.question_id && questionText && (
                  <p>{questionText}</p>
                )}
                {!replyBinding?.question_id && replyResult && (
                  <p className="detail-metadata">
                    {draft?.action === "approve"
                      ? "Approval"
                      : draft?.action === "changes"
                        ? "Feedback"
                        : "Review"}{" "}
                    for {replyResult.title}
                    {stale ? " (earlier context)" : ""}.
                  </p>
                )}
              </div>
            )}
            <ResourceRetry resources={[currentQuestion]} />
            <Composer
              draftKey={key}
              collapsed={!showComposer}
              projectId={projectId}
              taskId={task.id}
              inputRef={input}
              value={draft?.body ?? ""}
              onChange={updateBody}
              maxLength={8000}
              label={
                draft?.action === "changes"
                  ? "Feedback for this result"
                  : draft?.action === "approve"
                    ? "Testing notes or approval comment (optional)"
                    : composerAction === "answer"
                      ? "Your answer"
                      : "Task response"
              }
              disabled={busy || !inputEnabled}
              onBusy={setUploading}
              onSend={() => void send()}

              controls={settingsControl}
              action={
                showComposer && (
                  <Button
                    type="submit"
                    size={composerAction === "approve" ? "sm" : "icon-sm"}
                    aria-label={
                      draft?.action === "changes"
                        ? "Send feedback"
                        : composerAction === "approve"
                          ? "Approve and integrate"
                          : composerAction === "answer"
                            ? "Send answer"
                            : "Send message"
                    }
                    disabled={
                      busy ||
                      uploading ||
                      settingsDirty ||
                      !inputEnabled ||
                      !draft ||
                      (draft.action !== "approve" && !draft.body.trim()) ||
                      stale
                    }
                  >
                    {composerAction === "approve" ? (
                      "Approve and integrate"
                    ) : (
                      <ArrowUp size={16} />
                    )}
                  </Button>
                )
              }
            />
            {!gate.data?.enabled &&
              gate.data &&
              (showComposer || !!gate.data.pending_reply_id) && (
                <p className="muted">
                  {gate.data.pending_reply_id
                    ? "Reply waiting for the queue or a task update. Cancel to revise it."
                    : gate.data.reason === "reconcile_task"
                      ? "Ask the coordinator to update this task before continuing."
                      : gate.data.reason === "answer_pending"
                        ? "Answer saved. Waiting for the worker or coordinator to continue."
                        : "Task busy. Reply when processing finishes."}
                </p>
              )}
            {stale && (
              <p role="status">
                The context changed. Your draft still refers to the earlier
                question or result.{" "}
              </p>
            )}
            <div
              ref={setActionContext}
              className="task-action-context content-stack"
            />
            <div className="actions" role="group" aria-label="Task actions">
              <div
                ref={setActionHost}
                className="task-result-actions"
                hidden={
                  draft?.action === "changes" ||
                  draft?.action === "approve" ||
                  !!gate.data?.question_id
                }
              />

              {gate.data?.reason === "answer_editable" && !draft && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!currentQuestion.data}
                  onClick={() => {
                    if (!currentBinding || !currentQuestion.data) return;
                    beginDraft(
                      "answer",
                      currentBinding,
                      currentQuestion.data.answer ?? "",
                    );
                  }}
                >
                  Edit answer
                </Button>
              )}
              {Object.entries(drafts)
                .filter(([id, d]) => id !== key && d.body)
                .map(([id]) => (
                  <Button
                    key={id}
                    size="sm"
                    variant="outline"
                    type="button"
                    onClick={() => setChosen(id)}
                  >
                    Resume saved draft (
                    {id.startsWith("result:")
                      ? "earlier result"
                      : "earlier context"}
                    )
                  </Button>
                ))}

              {stale && draft?.action !== "approve" && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!gate.data?.enabled}
                  onClick={() => {
                    if (!currentBinding) return;
                    setDrafts((values) => ({
                      ...values,
                      [key]: {
                        ...draft!,
                        id: crypto.randomUUID(),
                        binding: currentBinding,
                        action: gate.data?.question_id
                          ? "answer"
                          : draft!.action,
                      },
                    }));
                  }}
                >
                  Use current context
                </Button>
              )}
              {draft && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy || uploading}
                  onClick={discardDraft}
                >
                  {composerAction === "answer"
                    ? gate.data?.reason === "answer_editable"
                      ? "Cancel edit"
                      : "Discard answer"
                    : composerAction === "changes"
                      ? "Cancel feedback"
                      : composerAction === "approve"
                        ? "Cancel approval"
                        : "Cancel message"}
                </Button>
              )}
              {gate.data?.pending_reply_id && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void cancelPending()}
                >
                  Cancel pending message
                </Button>
              )}
              <WorkerActions
                projectId={projectId}
                task={task}
                runId={
                  gate.data?.run_id ??
                  messages.filter((m) => m.kind === "attempt").at(-1)
                    ?.source_id ??
                  null
                }
                refresh={tick}
                changed={() => setRevision((n) => n + 1)}
              />
              {taskActions}
            </div>
          </form>
        </OverlayFooter>
      </ContentStack>
    </TaskActionHost.Provider>
  );
}
function merge(old: Message[], next: Message[]) {
  return [
    ...new Map([...old, ...next].map((item) => [item.id, item])).values(),
  ].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}
function MessageBody({ message, path }: { message: Message; path: string }) {
  const [full, setFull] = useState(false);
  const source = useResource<Message>(
    full ? `${path}/thread/${encodeURIComponent(message.id)}?full=true` : null,
    message.revision,
  );
  return (
    <>
      {message.question && (
        <p>
          <strong>{message.question}</strong>
        </p>
      )}
      {!!message.stages.length && (
        <StageSequence stages={message.stages} label="Stages at this update" />
      )}
      {message.body && <Markdown>{source.data?.body ?? message.body}</Markdown>}
      <ResourceRetry resources={[source]} />
      {message.truncated && !source.data && (
        <Button
          variant="outline"
          size="sm"
          disabled={source.loading}
          onClick={() => setFull(true)}
        >
          Read full entry
        </Button>
      )}
    </>
  );
}

function hasBody(message: Message) {
  return !!(
    message.body ||
    message.stages.length ||
    message.question_id ||
    message.earlier_id ||
    message.successor_id ||
    ["attempt", "result", "question", "definition"].includes(message.kind)
  );
}
