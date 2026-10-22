import { MessageBody } from "./TaskMessages";
import { useRetainedState, retainDirty } from "./retainedState";
import type { DirtyCallback } from "./retainedState";
import { QuestionPrompt } from "./QuestionPrompt";
import { QuestionChoices } from "./QuestionChoices";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { useEffect, useMemo, useRef, useState } from "react";
import { PaneFooter, usePaneBody } from "./EntityPane";
import { Button } from "@/components/ui/button";
import { Composer } from "./Composer";
import { ArrowUp } from "lucide-react";
import { ContentStack } from "./DetailLayout";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request, type Task, type WorkspaceBoard } from "./workspace";
import { Markdown } from "./Markdown";
import { Result } from "./Result";
import { ExecutionDetails } from "./Runs";
import { RunActivity } from "./RunActivity";
import { TaskActionHost, WorkerActions } from "./TaskActions";
import { WorkspaceLink } from "./WorkspaceLink";
import { requestHref } from "./requestLinks";

type Gate = components["schemas"]["InputEligibility"];
type Binding = components["schemas"]["ReplyCreate"]["binding"];
type Message = components["schemas"]["ThreadMessage"];
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
export function TaskResponse({
  projectId,
  projects,
  task,
  refresh,
  onDirty,
  onResolved,
  requestTarget,
}: {
  projectId: string;
  projects: WorkspaceBoard["projects"];
  task: Task;
  requestTarget: {
    kind: "question" | "result" | "intervention" | "review" | "integration";
    id: string;
  };
  refresh: unknown;
  onDirty: DirtyCallback;
  onResolved?: () => void;
}) {
  const path = `workspaces/${projectId}/tasks/${task.id}`;
  const [revision, setRevision] = useState(0);
  const tick = useMemo(() => ({ refresh, revision }), [refresh, revision]);
  const gate = useResource<Gate>(
    `${path}/input-eligibility${requestTarget.kind === "question" ? `?question_id=${encodeURIComponent(requestTarget.id)}` : ""}`,
    tick,
  );
  const currentAttemptId = gate.data?.run_id ?? gate.data?.latest_attempt?.id;
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
    requestTarget.kind === "question"
      ? requestTarget.id
      : gate.data?.question_id;
  const currentQuestion = useResource<components["schemas"]["Question"]>(
    questionId ? "workspaces/" + projectId + "/questions/" + questionId : null,
    tick,
  );
  const executionId =
    requestTarget.kind === "integration"
      ? `validation:${requestTarget.id}`
      : null;
  const execution = useResource<components["schemas"]["ExecutionItem"]>(
    executionId ? `${path}/executions/${executionId}` : null,
    tick,
  );
  const target =
    requestTarget.kind === "integration"
      ? execution.data
        ? `attempt:${execution.data.run_id}`
        : undefined
      : `${["intervention", "review"].includes(requestTarget.kind) ? "attempt" : requestTarget.kind}:${requestTarget.id}`;
  const selected = useResource<Message>(
    target ? `${path}/thread/${encodeURIComponent(target)}` : null,
    tick,
  );
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
    requestTarget.kind === "integration"
      ? execution.data?.run_id
      : requestTarget.id;
  const belongs = (binding: Draft["binding"]) =>
    requestTarget.kind === "question"
      ? binding.question_id === requestTarget.id
      : requestTarget.kind === "result"
        ? binding.result_id === requestTarget.id && !binding.question_id
        : !!requestedAttemptId &&
          currentAttemptId === requestedAttemptId &&
          !binding.question_id &&
          !binding.result_id;
  const selectedKey =
    chosen && drafts[chosen] && belongs(drafts[chosen].binding) ? chosen : null;
  const key =
    selectedKey ??
    (currentBinding && belongs(currentBinding)
      ? contextKey(currentBinding)
      : (Object.keys(drafts).find((key) => belongs(drafts[key].binding)) ??
        "request:" + requestTarget.kind + ":" + requestTarget.id));
  const draft = drafts[key];
  const matchesRequest =
    requestTarget.kind === "question"
      ? gate.data?.question_id === requestTarget.id
      : requestTarget.kind === "result"
        ? gate.data?.result_id === requestTarget.id && !gate.data?.question_id
        : !!requestedAttemptId &&
          currentAttemptId === requestedAttemptId &&
          !gate.data?.question_id &&
          !gate.data?.result_id;
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
  const [savedResult, setSavedResult] = useState<Version | null>(null);
  const [uploading, setUploading] = useState(false);
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
    if (composerAction && inputEnabled && (chosen || initialFocus))
      input.current?.focus({ preventScroll: true });
  }, [composerAction, inputEnabled, key, chosen, requestTarget.id, actionHost]);
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
  const replyBinding = draft?.binding ?? currentBinding;
  const replyResult =
    currentResult.data?.source_id === replyBinding?.result_id
      ? currentResult.data
      : selected.data?.kind === "result"
        ? selected.data
        : null;
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
        const saved = await request<Version>(
          `workspaces/${projectId}/results/${draft.binding.result_id}/review`,
          "POST",
          {
            expected_revision: draft.binding.result_revision,
            candidate_commit: draft.candidate,
            action: "approve",
            note: draft.body,
            author: "human",
          },
        );
        if (alive.current) setSavedResult(saved);
      } else await request(`${path}/replies`, "POST", draft);
      if (!alive.current) return;
      selected.invalidate();
      gate.invalidate();
      setDrafts((values) => {
        const next = { ...values };
        delete next[key];
        return next;
      });
      setChosen(null);
      onResolved?.();
      setRevision((n) => n + 1);
    } catch (e) {
      if (alive.current) reportError(e, "Could not submit reply");
    } finally {
      if (alive.current) setBusy(false);
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
    (matchesRequest || !!draft) &&
    ((!!currentResult.data &&
      (task.status !== "done" || task.state?.tone === "attention")) ||
      (!!currentAttempt.data &&
        [
          "preparing",
          "running",
          "stopping",
          "uncertain",
          "failed",
          "stopped",
        ].includes(currentAttempt.data.status ?? "")) ||
      !!gate.data?.question_id ||
      showComposer ||
      stale ||
      Object.values(drafts).some((saved) => !!saved.body) ||
      !!gate.data?.pending_reply_id);
  return (
    <TaskActionHost.Provider
      value={{ actions: actionHost, context: actionContext }}
    >
      <ContentStack
        space="section"
        className="request-response"
        data-announcement-source={`${projectId}:${requestTarget.kind}:${requestTarget.id}`}
      >
        <ResourceRetry
          resources={[
            selected,
            gate,
            execution,
            currentAttempt,
            currentResult,
            currentQuestion,
          ]}
        />
        {requestTarget.kind === "result" ? (
          <Result
            projectId={projectId}
            projects={projects}
            taskId={task.id}
            taskKey={task.key}
            taskTitle={task.title}
            versionId={requestTarget.id}
            refresh={tick}
            onReply={requestChanges}
            onApprove={approve}
            accepted={savedResult}
            inputEnabled={inputEnabled && !busy && !gate.data?.question_id}
          />
        ) : requestTarget.kind === "question" ? (
          currentQuestion.data && (
            <>
              <QuestionPrompt
                question={currentQuestion.data.question}
                context={currentQuestion.data.context}
                reviewContext={!!currentQuestion.data.origin_review_id}
                recommendation={currentQuestion.data.recommendation}
              />
              {currentQuestion.data.answer && (
                <ContentStack>
                  <strong>Your answer</strong>
                  <Markdown>{currentQuestion.data.answer}</Markdown>
                </ContentStack>
              )}
            </>
          )
        ) : (
          selected.data && (
            <>
              <MessageBody message={selected.data} path={path} />
              <RunActivity
                projectId={projectId}
                runId={selected.data.source_id}
              />
              {execution.data && (
                <ExecutionDetails
                  projectId={projectId}
                  taskKey={task.key}
                  item={execution.data}
                  refresh={tick}
                />
              )}
            </>
          )
        )}
        {requestTarget.kind === "result" &&
          gate.data?.result_id === requestTarget.id &&
          gate.data.question_id && (
            <WorkspaceLink
              to={requestHref(projectId, "question", gate.data.question_id)}
            >
              View request
            </WorkspaceLink>
          )}
        <PaneFooter>
          {showInputFooter && (
            <ContentStack className="task-input">
              <form
                className="content-stack"
                aria-label="Task input"
                onKeyDown={(event) => {
                  if (
                    event.key !== "Escape" ||
                    event.defaultPrevented ||
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
                {requestTarget.kind !== "question" && showComposer && (
                  <div className="task-input-context">
                    <strong>
                      {draft?.action === "approve"
                        ? "Approve and integrate"
                        : "Request changes"}
                    </strong>
                    {replyResult && (
                      <p className="detail-metadata">
                        For {replyResult.title}
                        {stale ? " (earlier context)" : ""}.
                      </p>
                    )}
                  </div>
                )}
                {!matchesRequest && (
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
                          !inputEnabled ||
                          !draft ||
                          (draft.action !== "approve" && !draft.body.trim()) ||
                          stale
                        }
                      >
                        {busy ? (
                          "Saving…"
                        ) : composerAction === "approve" ? (
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
                  {matchesRequest && (
                    <WorkerActions
                      projectId={projectId}
                      task={task}
                      runId={
                        currentAttemptId ??
                        (selected.data?.kind === "attempt"
                          ? selected.data.source_id
                          : null)
                      }
                      refresh={tick}
                      changed={() => setRevision((n) => n + 1)}
                    />
                  )}
                </div>
              </form>
            </ContentStack>
          )}
        </PaneFooter>
      </ContentStack>
    </TaskActionHost.Provider>
  );
}
