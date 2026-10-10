import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request, RequestError, type Task } from "./workspace";
import { Markdown } from "./Markdown";
import { Timestamp } from "./Timestamp";
import { AgentSettingsControl } from "./AgentSettings";
import { choiceLabel } from "./HarnessModels";
import { ActionTooltip } from "./ActionTooltip";
import { Composer } from "./Composer";
import { ContextRing } from "./ContextRing";
import { CoordinatorFullReply } from "./CoordinatorFullReply";
import { AgentPermissions } from "./AgentPermissions";
import { ActivityEntries } from "./RunActivity";
import { useFeedScroll } from "./useFeedScroll";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowUp, LoaderCircle, Square, X } from "lucide-react";
import { WorkspaceLink } from "./WorkspaceLink";
import { taskHref } from "./navigation";
type Page = components["schemas"]["CoordinatorPage"];
type Turn = components["schemas"]["CoordinatorTurn"];
const isActive = (page: Page) =>
  !!page.active && page.active.status !== "uncertain";
type Choice = components["schemas"]["AgentChoice-Output"];
type ChatDraft = {
  id: string;
  text: string;
  contextKey?: string;
  taskContext?: components["schemas"]["CoordinatorTaskSelection"] | null;
};
export type ChatDrafts = Map<string, ChatDraft>;

function mergeTurns(previous: Turn[], updates: Turn[]) {
  return [
    ...new Map(
      [...previous, ...updates].map((turn) => [turn.id, turn]),
    ).values(),
  ].sort((a, b) => a.number - b.number);
}

export function CoordinatorChat({
  projectId,
  refresh,
  drafts,
  onSettingsDirty,
  controlsActive = true,
  taskKey,
  selectedResultId,
}: {
  projectId: string;
  refresh: unknown;
  drafts: ChatDrafts;
  onSettingsDirty: (dirty: boolean) => void;
  controlsActive?: boolean;
  taskKey?: string;
  selectedResultId?: string;
}) {
  const selected = useResource<Task>(
    taskKey
      ? `projects/${projectId}/view/tasks/${encodeURIComponent(taskKey)}`
      : null,
    refresh,
  );
  const selectionKey = JSON.stringify([taskKey, selectedResultId]);
  const [taskFocus, setTaskFocus] = useState({
    key: selectionKey,
    dismissed: false,
  });
  if (taskFocus.key !== selectionKey)
    setTaskFocus({ key: selectionKey, dismissed: false });
  const attachTask =
    !!taskKey && (taskFocus.key !== selectionKey || !taskFocus.dismissed);
  const contextPending = attachTask && (!selected.data || !!selected.error);
  const taskContext =
    attachTask && selected.data
      ? {
          task_id: selected.data.id,
          task_revision: selected.data.revision,
          result_id: selectedResultId ?? null,
        }
      : null;
  const base = `projects/${projectId}/coordinator`;
  const [history, setHistory] = useState<{ page: Page | null; items: Turn[] }>({
    page: null,
    items: [],
  });
  const latest = history.items.at(-1)?.number;
  const path = latest ? `${base}?after=${latest}` : base;
  const [tick, setTick] = useState(0);
  const resource = useResource<Page>(path, `${refresh}:${tick}`, 10000, {
    projectId,
    attemptId: history.page?.active?.id,
    isActive,
  });
  const [before, setBefore] = useState<number | null | undefined>(undefined);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState<boolean | undefined>();
  const [controlsWereActive, setControlsWereActive] = useState(controlsActive);
  if (controlsWereActive !== controlsActive) {
    setControlsWereActive(controlsActive);
    if (!controlsActive && settingsOpen) setSettingsOpen(false);
  }
  const [choice, setChoice] = useState<Choice | null>(null);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [pendingPermissions, setPendingPermissions] = useState<string[]>([]);
  const [commands, setCommands] = useState<
    components["schemas"]["AgentCommand"][]
  >([]);
  const [commandsLoading, setCommandsLoading] = useState(false);
  const [commandsError, setCommandsError] = useState("");
  const [commandsFor, setCommandsFor] = useState("");
  const [commandsLoadedFor, setCommandsLoadedFor] = useState("");
  const commandsPending = useRef(false);
  const commandsFresh = useRef({ key: "", until: 0 });
  const commandsGeneration = useRef({ value: 0 });
  const commandChoice = JSON.stringify(choice);
  useEffect(() => {
    commandsPending.current = false;
    const generation = commandsGeneration.current;
    return () => {
      generation.value++;
    };
  }, [base, commandChoice]);
  const loadCommands = useCallback(
    async (reload = false) => {
      if (commandsPending.current) return;
      const key = base + commandChoice;
      if (
        !reload &&
        commandsFresh.current.key === key &&
        commandsFresh.current.until > Date.now()
      )
        return;
      if (commandChoice === "null") {
        setCommandsFor(key);
        commandsFresh.current = { key: "", until: 0 };
        setCommands([]);
        setCommandsLoading(false);
        setCommandsError(
          "Save a harness and its supported model settings first.",
        );
        return;
      }
      commandsPending.current = true;
      commandsFresh.current = { key: "", until: 0 };
      const generation = commandsGeneration.current.value;
      setCommandsFor(key);
      setCommands([]);
      setCommandsLoading(true);
      setCommandsError("");
      try {
        const discovered = await request<
          components["schemas"]["AgentCommand"][]
        >(
          `${base}/commands/discover?refresh=${reload}`,
          "POST",
          undefined,
          undefined,
          90000,
        );
        if (generation === commandsGeneration.current.value) {
          commandsFresh.current = { key, until: Date.now() + 300000 };
          setCommandsLoadedFor(key);
          setCommands(discovered);
        }
      } catch (e) {
        if (generation === commandsGeneration.current.value)
          setCommandsError((e as Error).message);
      } finally {
        if (generation === commandsGeneration.current.value) {
          commandsPending.current = false;
          setCommandsLoading(false);
        }
      }
    },
    [base, commandChoice],
  );
  const dirty = useCallback(
    (value: boolean) => {
      setSettingsDirty(value);
      onSettingsDirty(value);
    },
    [onSettingsDirty],
  );
  const key = projectId;
  const [draft, setDraft] = useState<ChatDraft>(
    () => drafts.get(key) ?? { id: crypto.randomUUID(), text: "" },
  );
  const [pane, setPane] = useState<HTMLDivElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const page = resource.data ?? history.page;
  if (page && history.page !== page) {
    // Preserve already displayed pages as the latest server window moves forward.
    setHistory({ page, items: mergeTurns(history.items, page.items) });
    if (before === undefined) setBefore(page.next_before ?? null);
  }
  const active = page?.active;
  const running = !!active && active.status !== "uncertain";
  const cursor = before;
  const turns = history.items;
  const scroll = useFeedScroll(content, !!page, undefined, true, pane);
  function update(text: string) {
    const next = {
      id: draft.text === text ? draft.id : crypto.randomUUID(),
      text,
    };
    drafts.set(key, next);
    setDraft(next);
  }
  async function send() {
    if (
      !draft.text.trim() ||
      busy ||
      uploading ||
      active ||
      !page ||
      !choice ||
      settingsDirty ||
      contextPending
    )
      return;
    setBusy(true);
    setError("");
    try {
      const contextKey = JSON.stringify([
        taskContext?.task_id,
        taskContext?.result_id,
      ]);
      const receipt = {
        ...draft,
        id: draft.contextKey === contextKey ? draft.id : crypto.randomUUID(),
        contextKey,
        taskContext:
          draft.contextKey === contextKey ? draft.taskContext : taskContext,
      };
      drafts.set(key, receipt);
      setDraft(receipt);
      await request(`${base}/messages`, "POST", {
        id: receipt.id,
        text: receipt.text,
        task_context: receipt.taskContext,
      });
      update("");
      setTick((n) => n + 1);
    } catch (e) {
      // A rejected stale selection has no effects. Other uncertain failures retain
      // the original receipt and context, even if live task revisions advance.
      if (e instanceof RequestError && e.code === "task_context_changed")
        update(draft.text);
      setError((e as Error).message);
      setTick((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }
  async function action(
    turn: Pick<Turn, "id">,
    operation: "stop" | "confirm-stopped" | "reset-session",
  ) {
    if (
      operation === "reset-session" &&
      !window.confirm(
        "Start a new agent session? Your chat stays here. The agent receives recent messages, without previous tool history.",
      )
    )
      return;
    if (
      operation === "confirm-stopped" &&
      !window.confirm(
        "Confirm you have stopped any remaining coordinator process on the host. This does not stop it for you.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await request(`${base}/turns/${turn.id}/${operation}`, "POST");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setTick((n) => n + 1);
    }
  }
  async function older() {
    if (!cursor || loadingEarlier) return;
    setLoadingEarlier(true);
    setError("");
    scroll.readingEarlier();
    try {
      const result = await request<Page>(`${base}?before=${cursor}`);
      setHistory((previous) => ({
        ...previous,
        items: mergeTurns(result.items, previous.items),
      }));
      setBefore(result.next_before);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingEarlier(false);
    }
  }
  return (
    <>
      <header className="workspace-pane-header">
        <h2>Coordinator</h2>
      </header>
      <div
        ref={setPane}
        className="coordinator-scroll"
        role="region"
        aria-label="Coordinator conversation"
        tabIndex={0}
      >
        <div ref={content} className="content-stack" data-space="section">
          {cursor && (
            <Button
              size="sm"
              variant="outline"
              disabled={loadingEarlier}
              onClick={() => void older()}
            >
              Load earlier messages
            </Button>
          )}
          {page?.welcome && cursor === null && (
            <article
              className="coordinator-message"
              aria-label="Coordinator welcome"
            >
              <div className="detail-metadata">Coordinator · Welcome</div>
              <Markdown>{page.welcome}</Markdown>
            </article>
          )}
          {turns.map((turn) => (
            <article
              key={turn.id}
              data-message-id={turn.id}
              className="coordinator-turn"
            >
              <div className="coordinator-message coordinator-human">
                <div className="detail-metadata">
                  You · <Timestamp date={turn.created_at} />
                </div>
                {turn.task_context && (
                  <WorkspaceLink
                    className="coordinator-task-reference"
                    title={`${turn.task_context.title} · task revision ${turn.task_context.task_revision}`}
                    to={
                      taskHref(projectId, turn.task_context) +
                      (turn.task_context.result_id
                        ? `/conversation/result:${encodeURIComponent(turn.task_context.result_id)}`
                        : "")
                    }
                  >
                    {turn.task_context.key}
                    {turn.task_context.result_id ? " · result" : ""}
                  </WorkspaceLink>
                )}
                <Markdown>{turn.text}</Markdown>
              </div>
              <div className="coordinator-message">
                {turn.activity.items.map((entry) =>
                  entry.kind === "agent" ? (
                    <div key={entry.key} data-kind="agent">
                      <Markdown>{entry.text}</Markdown>
                      {entry.omitted && (
                        <p className="muted">Reply preview shortened.</p>
                      )}
                    </div>
                  ) : (
                    <ActivityEntries key={entry.key} items={[entry]} />
                  ),
                )}
                {turn.activity.omitted && (
                  <p className="muted">Earlier activity is hidden.</p>
                )}
                {(turn.activity.omitted ||
                  turn.activity.items.some(
                    (entry) => entry.kind === "agent" && entry.omitted,
                  )) &&
                  !["starting", "running", "stopping", "uncertain"].includes(
                    turn.status,
                  ) && (
                    <CoordinatorFullReply
                      key={turn.activity.revision}
                      project={projectId}
                      turn={turn.id}
                      revision={turn.activity.revision}
                    />
                  )}
                {turn.notice && <p role="status">{turn.notice}</p>}
                {["starting", "running", "stopping"].includes(turn.status) &&
                  !resource.error && (
                    <div
                      role="status"
                      className="detail-metadata flex items-center gap-2"
                      aria-label="Coordinator activity"
                    >
                      {!pendingPermissions.includes(turn.id) && (
                        <LoaderCircle
                          size={14}
                          className="animate-spin motion-reduce:animate-none"
                          aria-hidden="true"
                        />
                      )}
                      {turn.status === "stopping"
                        ? "Stopping…"
                        : pendingPermissions.includes(turn.id)
                          ? "Waiting for permission"
                          : turn.status === "starting"
                            ? "Starting…"
                            : "Working…"}
                    </div>
                  )}
                <div className="detail-metadata" role="status">
                  Coordinator · {turn.status}
                </div>
              </div>
            </article>
          ))}
          <AgentPermissions
            projectId={projectId}
            role="coordinator"
            onPendingTurns={setPendingPermissions}
            refresh={`${refresh}:${tick}`}
          />
        </div>
      </div>
      <div className="coordinator-composer content-stack">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {resource.error && (
          <Alert variant="destructive">
            <AlertDescription>
              {resource.error}{" "}
              <Button variant="link" onClick={() => setTick((n) => n + 1)}>
                Retry loading messages
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {active?.status === "uncertain" && (
          <Alert>
            <AlertDescription>
              {active.notice}{" "}
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void action(active, "confirm-stopped")}
              >
                Confirm coordinator stopped
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {page?.session_recovery_turn_id && (
          <Alert>
            <AlertDescription>
              The saved agent session is unavailable. You can retry after fixing
              the host, or start a new session with recent chat.
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() =>
                  void action(
                    { id: page.session_recovery_turn_id! },
                    "reset-session",
                  )
                }
              >
                Start new session
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {attachTask && (
          <div
            className="coordinator-task-context"
            role="group"
            aria-label="Message task context"
          >
            {selected.data ? (
              <WorkspaceLink
                to={
                  taskHref(projectId, selected.data) +
                  (selectedResultId
                    ? `/conversation/result:${encodeURIComponent(selectedResultId)}`
                    : "")
                }
                title={selected.data.title}
              >
                {selected.data.key} · {selected.data.title}
                {selectedResultId ? " · selected result" : ""}
              </WorkspaceLink>
            ) : (
              <span>
                {selected.error
                  ? "Task context unavailable"
                  : "Loading task context…"}
              </span>
            )}
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Remove task context"
              onClick={() =>
                setTaskFocus({ key: selectionKey, dismissed: true })
              }
            >
              <X size={12} />
            </Button>
          </div>
        )}
        <Composer
          projectId={projectId}
          nativeCommands={{
            items: commandsLoadedFor === base + commandChoice ? commands : [],
            loaded: commandsLoadedFor === base + commandChoice,
            loading: commandsLoading && commandsFor === base + commandChoice,
            error: commandsFor === base + commandChoice ? commandsError : "",
            load: loadCommands,
          }}
          active={controlsActive}
          value={draft.text}
          onChange={update}
          label="Message coordinator"
          placeholder="Message coordinator…"
          maxLength={16000}
          disabled={busy}
          onBusy={setUploading}
          onSend={() => void send()}

          controls={
            <>
              <AgentSettingsControl
                projectId={projectId}
                path={`projects/${projectId}/coordinator-settings`}
                refresh={refresh}
                coordinator
                coordinatorActive={!!active}
                open={controlsActive && (settingsOpen ?? (!choice && !taskKey))}
                autoOpened={settingsOpen === undefined && !choice && !taskKey}
                onOpenChange={setSettingsOpen}
                onDirty={dirty}
                onReady={setChoice}
                label={choice ? choiceLabel(choice) : "Model settings"}
              />
              <ContextRing
                context={active?.activity.context ?? page?.context}
                active={running && !!active?.activity.context}
              />
            </>
          }
          action={
            running ? (
              <ActionTooltip
                label={active.status === "stopping" ? "Stopping…" : "Stop"}
                disabled={busy || active.status === "stopping"}
              >
                <Button
                  type="button"
                  size="icon-sm"
                  variant="secondary"
                  aria-label={
                    active.status === "stopping" ? "Stopping…" : "Stop"
                  }
                  disabled={busy || active.status === "stopping"}
                  onClick={() => void action(active, "stop")}
                >
                  <Square size={14} />
                </Button>
              </ActionTooltip>
            ) : (
              <Button
                type="button"
                size="icon-sm"
                aria-label="Send"
                disabled={
                  busy ||
                  uploading ||
                  !!active ||
                  !page ||
                  !choice ||
                  settingsDirty ||
                  contextPending ||
                  !draft.text.trim()
                }
                onClick={() => void send()}
              >
                <ArrowUp size={16} />
              </Button>
            )
          }
        />
      </div>
    </>
  );
}
