import { LoadMore } from "./LoadMore";
import { ActivityEntry } from "./ActivityEntry";
import { useRetainedState, retainDirty } from "./retainedState";
import type { DirtyCallback } from "./retainedState";
import { QuestionPrompt } from "./QuestionPrompt";
import { QuestionChoices } from "./QuestionChoices";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  Fragment,
} from "react";
import { useParams } from "react-router";
import { PaneFooter, usePaneBody } from "./EntityPane";
import { Button } from "@/components/ui/button";
import { Composer } from "./Composer";
import { AgentSettingsControl } from "./AgentSettings";
import { choiceLabel } from "./HarnessModels";
import { ArrowUp } from "lucide-react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
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

export function TaskConversation({
  projectId,
  task,
  board,
  refresh,
  onDirty,
  onSettingsDirty,
  requestTarget,
}: {
  projectId: string;
  task: Task;
  board: Pick<Board, "milestones" | "tasks">;
  requestTarget?: {
    kind: "question" | "result" | "intervention" | "review" | "integration";
    id: string;
  };
  refresh: unknown;
  onDirty: DirtyCallback;
  onSettingsDirty: (dirty: boolean) => void;
}) {
  const route = useParams();
  const overview = !requestTarget && (!route.tab || route.tab === "task");
  const path = `projects/${projectId}/tasks/${task.id}`;
  const [revision, setRevision] = useState(0);
  const tick = useMemo(() => ({ refresh, revision }), [refresh, revision]);
  const page = useResource<Page>(`${path}/thread`, tick);
  const gate = useResource<Gate>(
    `${path}/input-eligibility${requestTarget?.kind === "question" ? `?question_id=${encodeURIComponent(requestTarget.id)}` : ""}`,
    tick,
  );
  const currentAttemptId =
    gate.data?.run_id ??
    task.state?.href?.match(/\/conversation\/attempt:([^/?#]+)/)?.[1];
  const currentAttempt = useResource<Message>(
    currentAttemptId ? `${path}/thread/attempt:${currentAttemptId}` : null,
    tick,
  );
  const currentResult = useResource<Message>(
    gate.data?.result_id
      ? `${path}/thread/result:${gate.data.result_id}`
      : null,
    tick,
  );
  const questionId =
    requestTarget?.kind === "question"
      ? requestTarget.id
      : gate.data?.question_id;
  const currentQuestion = useResource<components["schemas"]["Question"]>(
    questionId ? "projects/" + projectId + "/questions/" + questionId : null,
    tick,
  );
  const initial = useResource<Message>(`${path}/thread/definition:1`, task.id);
  const executionId =
    requestTarget?.kind === "integration"
      ? `validation:${requestTarget.id}`
      : ["history", "runs"].includes(route.tab ?? "") && route.runId
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
    requestTarget?.kind === "integration"
      ? execution.data
        ? `attempt:${execution.data.run_id}`
        : undefined
      : requestTarget
        ? `${requestTarget.kind === "intervention" || requestTarget.kind === "review" ? "attempt" : requestTarget.kind}:${requestTarget.id}`
        : route.tab === "conversation"
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
  const [drafts, setDrafts] = useRetainedState<Record<string, Draft>>(
    `${path}:drafts`,
    {},
  );
  const [chosen, setChosen] = useRetainedState<string | null>(
    `${path}:chosen`,
    null,
  );
  const currentBinding = gate.data ? binding(gate.data) : null;
  const requestedAttemptId =
    requestTarget?.kind === "integration"
      ? execution.data?.run_id
      : requestTarget?.id;
  const belongs = (binding: Draft["binding"]) =>
    !requestTarget ||
    (requestTarget.kind === "question"
      ? binding.question_id === requestTarget.id
      : requestTarget.kind === "result"
        ? binding.result_id === requestTarget.id && !binding.question_id
        : !!requestedAttemptId &&
          currentAttemptId === requestedAttemptId &&
          !binding.question_id &&
          !binding.result_id);
  const selectedKey =
    chosen && drafts[chosen] && belongs(drafts[chosen].binding) ? chosen : null;
  const key =
    selectedKey ??
    (currentBinding && belongs(currentBinding)
      ? contextKey(currentBinding)
      : (Object.keys(drafts).find((key) => belongs(drafts[key].binding)) ??
        (requestTarget
          ? "request:" + requestTarget.kind + ":" + requestTarget.id
          : "task")));
  const draft = drafts[key];
  const matchesRequest =
    !requestTarget ||
    (requestTarget.kind === "question"
      ? gate.data?.question_id === requestTarget.id
      : requestTarget.kind === "result"
        ? gate.data?.result_id === requestTarget.id && !gate.data?.question_id
        : !!requestedAttemptId &&
          currentAttemptId === requestedAttemptId &&
          !gate.data?.question_id &&
          !gate.data?.result_id);
  const inputEnabled =
    matchesRequest &&
    !!gate.data?.enabled &&
    (gate.data.reason !== "answer_editable" || !!draft);
  // A selected draft stays open even when empty; eligibility controls delivery.
  const composerAction =
    draft?.action ??
    (matchesRequest &&
    gate.data?.question_id &&
    gate.data.reason !== "answer_editable"
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
  const paneBody = usePaneBody();
  const input = useRef<HTMLTextAreaElement>(null);
  const [actionHost, setActionHost] = useState<HTMLDivElement | null>(null);
  const [actionContext, setActionContext] = useState<HTMLDivElement | null>(
    null,
  );
  useEffect(() => {
    const focused = document.activeElement;
    const initialFocus =
      focused === document.body ||
      focused?.classList.contains("entity-pane-body");
    // Late eligibility reads must not steal focus from evidence or other controls.
    // Choosing a reply/draft explicitly still moves into its composer.
    if (
      composerAction &&
      inputEnabled &&
      (chosen || (initialFocus && !requestTarget?.id))
    )
      input.current?.focus({ preventScroll: true });
  }, [composerAction, inputEnabled, key, chosen, requestTarget?.id]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const dirty = Object.values(drafts).some(
      (d) => !!d.body || d.action === "approve",
    );
    retainDirty(`${path}:drafts`, dirty);
    onDirty(busy || uploading || dirty, !busy && !uploading);
    return () => onDirty(false);
  }, [drafts, busy, uploading, onDirty, path]);
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
  const coordinatorQuestion =
    !!replyBinding?.question_id &&
    replyBinding.question_id === currentQuestion.data?.id &&
    !currentQuestion.data.origin_run_id;
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
    !requestTarget &&
      !overview &&
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
        candidate:
          version.candidate_commit ?? version.source_commit ?? undefined,
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
      !matchesRequest ||
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
    if (!matchesRequest || !gate.data?.pending_reply_id) return;
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
  const showInputFooter =
    (!requestTarget || matchesRequest || !!draft) &&
    (gate.data?.reason !== "review_owns_result" ||
      !!draft ||
      !!gate.data?.question_id) &&
    (task.status !== "done" ||
      task.state?.tone === "attention" ||
      !!task.reconciliation_reason ||
      showComposer ||
      stale ||
      Object.values(drafts).some((saved) => !!saved.body) ||
      !!gate.data?.pending_reply_id);
  return (
    <TaskActionHost.Provider
      value={{ actions: actionHost, context: actionContext }}
    >
      <ContentStack ref={root} space="section" className="task-conversation">
        <ContentStack>
          <ResourceRetry
            resources={[page, selected, gate, execution, currentAttempt]}
          />
          {page.loading && !page.data && <p>Loading activity…</p>}
          {overview && (
            <>
              {currentAttempt.data?.body &&
                ["failed", "stopped", "uncertain"].includes(
                  currentAttempt.data.status ?? "",
                ) && (
                  <DetailSection title={currentAttempt.data.title}>
                    <Markdown>{currentAttempt.data.body}</Markdown>
                    <WorkspaceLink
                      to={messageHref(projectId, task, currentAttempt.data.id)}
                    >
                      Inspect attempt
                    </WorkspaceLink>
                  </DetailSection>
                )}
              {currentQuestion.data?.status === "open" && (
                <QuestionPrompt
                  question={currentQuestion.data.question}
                  context={currentQuestion.data.context}
                  reviewContext={!!currentQuestion.data.origin_review_id}
                  recommendation={currentQuestion.data.recommendation}
                  verificationUrl={currentQuestion.data.verification_url}
                />
              )}
              {gate.data?.result_id && (
                <Result
                  overview
                  projectId={projectId}
                  taskId={task.id}
                  taskKey={task.key}
                  versionId={gate.data.result_id}
                  refresh={tick}
                  onReply={requestChanges}
                  onApprove={approve}
                  inputEnabled={inputEnabled && !gate.data.question_id}
                />
              )}
            </>
          )}
          {!overview && (
            <ol className="conversation-messages" aria-label="Task feed">
              {messages
                .filter(
                  (message) =>
                    (!requestTarget && !message.technical) ||
                    message.id === target ||
                    message.id === selected.data?.id,
                )
                .map((message, index) => (
                  <Fragment key={message.id}>
                    {!requestTarget && index === 0 && cursor && (
                      <li>
                        <LoadMore
                          variant="outline"
                          size="sm"
                          disabled={olderBusy}
                          onClick={() => void older()}
                        >
                          Load earlier activity
                        </LoadMore>
                      </li>
                    )}
                    <li
                      data-announcement-source={
                        message.source_id
                          ? message.kind === "attempt"
                            ? `${projectId}:intervention:${message.source_id} ${projectId}:review:${message.source_id}`
                            : `${projectId}:${message.kind}:${message.source_id}`
                          : undefined
                      }
                      data-message-id={message.id}
                      data-kind={message.kind}
                      className={
                        requestTarget ? "request-content" : "task-activity-item"
                      }
                    >
                      <ActivityEntry
                        identity={path + ":" + message.id}
                        selected={
                          target === message.id ||
                          message === activeAttempt ||
                          (!route.runId &&
                            ["changes", "result"].includes(route.tab ?? "") &&
                            message.source_id === gate.data?.result_id)
                        }
                        title={message.title}
                        state={message.state}
                        inline={!!requestTarget}
                        hasBody={hasBody(message)}
                        metadata={
                          <>
                            {message.actor.label} ·{" "}
                            <ExactTimeTooltip date={message.created_at}>
                              <WorkspaceLink
                                to={messageHref(projectId, task, message.id)}
                                aria-label={"Permalink: " + message.title}
                              >
                                <Timestamp
                                  date={message.created_at}
                                  tooltip={false}
                                />
                              </WorkspaceLink>
                            </ExactTimeTooltip>
                          </>
                        }
                      >
                        {hasBody(message) && (
                          <div
                            className={
                              requestTarget
                                ? "content-stack"
                                : "conversation-message-body content-stack"
                            }
                          >
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
                                  inputEnabled && !gate.data?.question_id
                                }
                              />
                            ) : (
                              <>
                                {requestTarget?.kind === "question"
                                  ? currentQuestion.data && (
                                      <QuestionPrompt
                                        question={currentQuestion.data.question}
                                        context={currentQuestion.data.context}
                                        reviewContext={
                                          !!currentQuestion.data
                                            .origin_review_id
                                        }
                                        verificationUrl={
                                          currentQuestion.data.verification_url
                                        }
                                        recommendation={
                                          currentQuestion.data.recommendation
                                        }
                                      />
                                    )
                                  : message.kind !== "definition" && (
                                      <MessageBody
                                        message={message}
                                        path={path}
                                      />
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
                                      to={messageHref(
                                        projectId,
                                        task,
                                        message.id,
                                      )}
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
                      </ActivityEntry>
                    </li>
                  </Fragment>
                ))}
            </ol>
          )}
          {!overview &&
            !requestTarget &&
            messages.some((message) => message.technical) && (
              <Disclosure summary="Technical history">
                <ContentStack>
                  {messages
                    .filter((message) => message.technical)
                    .map((message) => (
                      <div
                        key={message.id}
                        className="flex flex-wrap items-center justify-between gap-2"
                      >
                        <WorkspaceLink
                          to={messageHref(projectId, task, message.id)}
                        >
                          {message.title}
                        </WorkspaceLink>
                        <Timestamp date={message.created_at} />
                      </div>
                    ))}
                </ContentStack>
              </Disclosure>
            )}
        </ContentStack>
        <PaneFooter>
          {showInputFooter && (
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
                  paneBody?.focus({ preventScroll: true }),
                );
              }}
              onSubmit={(event) => {
                event.preventDefault();
                void send();
              }}
            >
              {!(requestTarget?.kind === "question") &&
                (showComposer ||
                  (!requestTarget &&
                    matchesRequest &&
                    replyResult?.status === "ready" &&
                    inputEnabled)) && (
                  <div className="task-input-context">
                    <strong>
                      {replyBinding?.question_id
                        ? "Answer the question"
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
              {requestTarget && !matchesRequest && (
                <p role="status">
                  This request is no longer awaiting a response. Your saved
                  response is available in its history.
                </p>
              )}
              <ResourceRetry resources={[currentQuestion]} />
              {composerAction === "answer" && currentQuestion.data && (
                <QuestionChoices
                  choices={currentQuestion.data.choices}
                  choose={updateBody}
                  disabled={busy || !inputEnabled || stale}
                />
              )}
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

                controls={
                  coordinatorQuestion || requestTarget
                    ? undefined
                    : settingsControl
                }
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
              {!showComposer &&
                currentQuestion.data?.status !== "open" &&
                currentQuestion.data?.delivery && (
                  <p>{currentQuestion.data.delivery.message}</p>
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

                {matchesRequest &&
                  gate.data?.reason === "answer_editable" &&
                  !draft && (
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
                  .filter(
                    ([id, d]) => id !== key && d.body && belongs(d.binding),
                  )
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

                {!requestTarget && stale && draft?.action !== "approve" && (
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
                {matchesRequest && gate.data?.pending_reply_id && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void cancelPending()}
                  >
                    Cancel pending message
                  </Button>
                )}
                {(!requestTarget || matchesRequest) && (
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
                )}
              </div>
            </form>
          )}
        </PaneFooter>
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
    message.question_id ||
    message.earlier_id ||
    message.successor_id ||
    ["attempt", "result", "question", "definition"].includes(message.kind)
  );
}
