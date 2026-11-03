import { ConfirmButton } from "./ConfirmButton";
import { LoadMore } from "./LoadMore";
import { useRetainedState, retainDirty } from "./retainedState";
import type { DirtyCallback } from "./retainedState";
import { Composer } from "./Composer";
import { QuestionPrompt } from "./QuestionPrompt";
import { QuestionChoices } from "./QuestionChoices";
import { ScopedAnchor } from "./WorkspaceLink";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { authorLabel } from "./workspace";
import {
  ContentStack,
  Disclosure,
  DetailSection,
  DetailHeading,
} from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { Button } from "@/components/ui/button";
import type { components } from "./api-schema";
import { TooltipLink } from "./TooltipLink";
import type { TaskReference } from "./workspace";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import { followLink, workspaceHref, taskHref } from "./navigation";
import { request } from "./workspace";
import { PaneFooter } from "./EntityPane";

export type Question = components["schemas"]["Question"] & {
  truncated_fields?: Record<string, unknown>;
};
type Page = { items: Question[]; next_cursor: number | null };
export function questionHref(projectId: string, id: string) {
  return `${workspaceHref(projectId)}/requests/question/${encodeURIComponent(id)}`;
}

function PreviousAnswers({
  path,
  revision,
}: {
  path: string;
  revision: number;
}) {
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function read(before: number) {
    setBusy(true);
    setError("");
    try {
      const result = await request<Page>(`${path}/history?before=${before}`);
      setPage((previous) => ({
        ...result,
        items: [...(previous?.items ?? []), ...result.items],
      }));
    } catch (e) {
      setError((e as Error).message);
      reportError(e, "Could not load previous answers");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Disclosure
      summary={<>Earlier responses</>}
      className="previous-answers"
      onToggle={(e) => {
        if (e.currentTarget.open && !page && !busy) void read(revision);
      }}
    >
      {page?.items
        .filter((q) => q.status === "answered")
        .map((q) => (
          <div className="answer-history content-stack" key={q.revision}>
            <DetailHeading
              entry
              title={q.question}
              metadata={
                <>
                  {authorLabel(q.updated_by)} ·{" "}
                  <Timestamp date={q.updated_at} />
                </>
              }
            />
            <Markdown>{q.answer ?? ""}</Markdown>
            {!!q.truncated_fields?.answer && (
              <Button
                size="sm"
                variant="link"
                className="text-button"
                onClick={() =>
                  void request<Question>(`${path}?revision=${q.revision}`)
                    .then((full) =>
                      setPage(
                        (p) =>
                          p && {
                            ...p,
                            items: p.items.map((item) =>
                              item.revision === full.revision ? full : item,
                            ),
                          },
                      ),
                    )
                    .catch((e) =>
                      reportError(e, "Could not load full response"),
                    )
                }
              >
                Read full response
              </Button>
            )}
          </div>
        ))}
      <ResourceRetry
        resources={[
          {
            error,
            loading: busy,
            retry: () => void read(page?.next_cursor ?? revision),
          },
        ]}
      />
      {busy && <p>Loading…</p>}
      {page?.next_cursor && (
        <LoadMore
          size="sm"
          variant="outline"
          className="quiet"
          disabled={busy}
          onClick={() => void read(page.next_cursor!)}
        >
          Older responses
        </LoadMore>
      )}
      {page &&
        !page.items.some((q) => q.status === "answered") &&
        !page.next_cursor && <p>No earlier answers.</p>}
    </Disclosure>
  );
}

export function QuestionDetail({
  tasks,
  projectId,
  identity,
  refresh,
  onDirty,
  open,
}: {
  projectId: string;
  identity: string;
  tasks: TaskReference[];
  refresh: unknown;
  onDirty: DirtyCallback;
  open: (path: string) => void;
}) {
  const path = `workspaces/${encodeURIComponent(projectId)}/questions/${encodeURIComponent(identity)}`;
  const [loaded, setLoaded] = useRetainedState<Question | null>(
    `${path}:loaded`,
    null,
  );
  const [incoming, setIncoming] = useState<Question | null>(null);
  const [draft, setDraft] = useRetainedState(`${path}:draft`, "");
  const [correcting, setCorrecting] = useRetainedState(
    `${path}:correcting`,
    false,
  );
  const [correctionLink, setCorrectionLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const dirty = !!loaded && (loaded.status === "open" || correcting) && !!draft;
  const dirtyRef = useRef(dirty);
  useEffect(() => {
    dirtyRef.current = dirty || busy || uploading;
  }, [dirty, busy, uploading]);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const focused = document.activeElement;
    const initial =
      focused === document.body ||
      focused?.classList.contains("entity-pane-body");
    if ((loaded?.status === "open" || correcting) && (initial || correcting))
      input.current?.focus({ preventScroll: true });
  }, [loaded?.status, correcting, identity]);
  const generation = useRef(0);
  useLayoutEffect(() => {
    retainDirty(`${path}:draft`, dirty);
    onDirty(dirty || busy || uploading, !busy && !uploading);
    return () => onDirty(false);
  }, [dirty, busy, uploading, onDirty, path]);
  useEffect(() => {
    const current = ++generation.current;
    request<Question>(path)
      .then((q) => {
        if (current !== generation.current) return;
        setIncoming(q);
        if (!dirtyRef.current) {
          setLoaded((old) => (!old || q.revision >= old.revision ? q : old));
          setDraft("");
        }
        if (!dirtyRef.current) setError("");
      })
      .catch((e) => {
        if (current === generation.current) {
          setError(e.message);
          reportError(e, "Could not load question", true);
        }
      });
    return () => {
      generation.current += 1;
    };
  }, [path, refresh, retry, setDraft, setLoaded]);
  async function answer() {
    if (
      !loaded ||
      busy ||
      uploading ||
      !draft.trim() ||
      (incoming && incoming.revision > loaded.revision)
    )
      return;
    setBusy(true);
    setError("");
    try {
      const saved = await request<Question>(
        `${path}/${correcting ? "correction" : "answer"}`,
        "POST",
        {
          expected_revision: loaded.revision,
          answer: draft,
          author: "human",
        },
      );
      generation.current += 1;
      setDraft("");
      if (correcting) {
        setCorrecting(false);
        try {
          const current = await request<Question>(path);
          setLoaded(current);
          setIncoming(current);
        } catch (error) {
          setError((error as Error).message);
          reportError(error, "Correction saved; could not refresh question");
        }
        setCorrectionLink(questionHref(projectId, saved.id));
      } else {
        setLoaded(saved);
        setIncoming(saved);
      }
    } catch (e) {
      reportError(e, "Could not save answer");
    } finally {
      setBusy(false);
    }
  }
  const newer = loaded && incoming && incoming.revision > loaded.revision;
  const correctionHref =
    correctionLink ??
    (loaded?.correction_question_id
      ? questionHref(projectId, loaded.correction_question_id)
      : null);
  return (
    <section
      data-announcement-source={
        loaded ? `${projectId}:question:${loaded.id}` : undefined
      }
      className="question-detail content-stack"
      data-space="section"
      aria-label="Question content"
    >
      {error && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setRetry((v) => v + 1)}
        >
          Reload question
        </Button>
      )}
      {!loaded ? (
        !error && <p>Loading question…</p>
      ) : (
        <>
          <QuestionPrompt
            question={loaded.question}
            context={loaded.context}
            reviewContext={!!loaded.origin_review_id}
            recommendation={loaded.recommendation}
          />
          {newer && (
            <div className="notice content-stack">
              <p>This question changed while you were answering.</p>
              <ConfirmButton
                size="sm"
                variant="outline"
                disabled={busy || uploading}
                requiresConfirmation={dirty}
                binding={JSON.stringify([
                  path,
                  loaded.revision,
                  incoming?.revision,
                  draft,
                ])}
                title="Discard your draft and load the latest question?"
                description="The latest question will replace your unsaved answer."
                action={() => {
                  setLoaded(incoming);
                  setDraft("");
                  setError("");
                }}
              >
                Load latest
              </ConfirmButton>
            </div>
          )}
          {correctionHref && (
            <p>
              Correction saved.{" "}
              <ScopedAnchor
                href={correctionHref}
                onClick={(event) =>
                  followLink(event, () => open(correctionHref))
                }
              >
                View correction
              </ScopedAnchor>
              .
            </p>
          )}
          {loaded.delivery && loaded.status !== "open" && (
            <ContentStack>
              <p role="status">{loaded.delivery.state}</p>
              {loaded.delivery.message && <p>{loaded.delivery.message}</p>}
            </ContentStack>
          )}
          {loaded.previous_question_id && (
            <ScopedAnchor
              href={questionHref(projectId, loaded.previous_question_id)}
              onClick={(event) =>
                followLink(event, () =>
                  open(questionHref(projectId, loaded.previous_question_id!)),
                )
              }
            >
              View earlier answer
            </ScopedAnchor>
          )}
          {!loaded.delivery && loaded.status === "answered" && (
            <p>Answer saved. Ask the coordinator to apply it.</p>
          )}
          {correcting &&
            (loaded.continuation_run_id || loaded.consumed_answer_revision) && (
              <p>
                Work already has the earlier answer. The Coordinator will need
                to reconcile this correction.
              </p>
            )}
          {loaded.affected_task_keys.length > 0 && (
            <DetailSection
              className="question-section"
              title={<>Affected tasks</>}
            >
              <div className="question-meta">
                {loaded.affected_task_keys.map((key) => (
                  <TooltipLink
                    className="task-key"
                    tooltip={
                      tasks.find((task) => task.key === key)?.title ?? key
                    }
                    key={key}
                    href={taskHref(projectId, { key })}
                    onClick={(e) =>
                      followLink(e, () => open(taskHref(projectId, { key })))
                    }
                  >
                    {key}
                  </TooltipLink>
                ))}
              </div>
            </DetailSection>
          )}
          {loaded.answer && (
            <DetailSection
              className="question-section"
              title={<>Your answer</>}
            >
              <Markdown>{loaded.answer}</Markdown>
            </DetailSection>
          )}
          {loaded.decision && (
            <DetailSection
              className="question-section"
              title={
                <>
                  {loaded.status === "applied"
                    ? "Applied decision"
                    : "Reason withdrawn"}
                </>
              }
            >
              <Markdown>{loaded.decision}</Markdown>
            </DetailSection>
          )}
          <PaneFooter>
            {(correcting || loaded.status === "open" || loaded.can_correct) && (
              <div className="task-input content-stack">
                {correcting || loaded.status === "open" ? (
                  <form
                    aria-label="Question response"
                    className="answer-form content-stack"
                    data-space="content"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void answer();
                    }}
                  >
                    <QuestionChoices
                      choices={loaded.choices}
                      choose={setDraft}
                      disabled={busy || !!newer}
                    />
                    <Composer
                      inputRef={input}
                      projectId={projectId}
                      taskId={loaded.task_id ?? undefined}
                      draftKey={`${loaded.id}:${loaded.revision}`}
                      value={draft}
                      onChange={setDraft}
                      label="Your answer"
                      maxLength={8000}
                      disabled={busy}
                      onBusy={setUploading}
                      onSend={() => void answer()}
                    />
                    <div className="actions">
                      <Button
                        type="submit"
                        size="sm"
                        aria-label={
                          correcting ? "Send correction" : "Send answer"
                        }
                        disabled={busy || uploading || !!newer || !draft.trim()}
                      >
                        {busy
                          ? "Saving…"
                          : correcting
                            ? "Send correction"
                            : "Send answer"}
                      </Button>
                      {(correcting || dirty) && (
                        <ConfirmButton
                          size="sm"
                          variant="outline"
                          className="quiet"
                          disabled={busy || uploading}
                          requiresConfirmation={dirty}
                          binding={JSON.stringify([
                            path,
                            loaded.revision,
                            draft,
                            correcting,
                          ])}
                          actionLabel="Discard answer"
                          title="Discard your unsaved answer?"
                          description="Your answer has not been saved."
                          action={() => {
                            setCorrecting(false);
                            setDraft("");
                          }}
                        >
                          Cancel
                        </ConfirmButton>
                      )}
                    </div>
                  </form>
                ) : (
                  <>
                    {loaded.can_correct && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setCorrecting(true);
                          setDraft("");
                        }}
                      >
                        Send correction
                      </Button>
                    )}
                  </>
                )}
              </div>
            )}
          </PaneFooter>
          {loaded.answer_count > (loaded.answer ? 1 : 0) && (
            <PreviousAnswers
              key={loaded.revision}
              path={path}
              revision={loaded.revision}
            />
          )}
        </>
      )}
    </section>
  );
}
