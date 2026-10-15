import type { AdjacentContext } from "./AdjacentView";
import { toast } from "sonner";
import { reportError } from "./requestFeedback";
import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request, RequestError } from "./workspace";
import { Markdown } from "./Markdown";
import { Timestamp } from "./Timestamp";
import { AgentSettingsControl } from "./AgentSettings";
import { choiceLabel } from "./HarnessModels";
import { ActionTooltip } from "./ActionTooltip";
import { Composer } from "./Composer";
import { ContextRing } from "./ContextRing";
import { CoordinatorFullReply } from "./CoordinatorFullReply";
import { AgentPermissions } from "./AgentPermissions";
import { CoordinatorActivity } from "./CoordinatorActivity";
import { useFeedScroll } from "./useFeedScroll";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowUp, LoaderCircle, Square } from "lucide-react";
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
  context?: AdjacentContext;
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
  workspaceId,
  viewPath,
  adjacent,
  refresh,
  drafts,
  onSettingsDirty,
  controlsActive = true,
}: {
  workspaceId: string;
  viewPath: string;
  adjacent: AdjacentContext | null;
  refresh: unknown;
  drafts: ChatDrafts;
  onSettingsDirty: (dirty: boolean) => void;
  controlsActive?: boolean;
}) {
  const contextPending = !adjacent;
  const taskContext = adjacent?.task;
  const base = `workspaces/${workspaceId}/coordinator`;
  const [history, setHistory] = useState<{ page: Page | null; items: Turn[] }>({
    page: null,
    items: [],
  });
  const latest = history.items.at(-1)?.number;
  const path = latest ? `${base}?after=${latest}` : base;
  const [tick, setTick] = useState(0);
  const resource = useResource<Page>(path, `${refresh}:${tick}`, 10000, {
    workspaceId,
    attemptId: history.page?.active?.id,
    isActive,
  });
  const [before, setBefore] = useState<number | null | undefined>(undefined);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
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
        toast.info("Save a harness and its supported model settings first.");
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
        if (generation === commandsGeneration.current.value) {
          setCommandsError((e as Error).message);
          reportError(e, "Could not load coordinator commands");
        }
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
  const key = workspaceId;
  const [draft, setDraft] = useState<ChatDraft>(
    () => drafts.get(key) ?? { id: crypto.randomUUID(), text: "" },
  );
  const [impact, setImpact] = useState<string | null>(null);
  useEffect(() => {
    function discuss(event: Event) {
      const detail = (
        event as CustomEvent<{ workspaceId: string; text: string }>
      ).detail;
      if (detail.workspaceId !== workspaceId) return;
      if (drafts.get(key)?.text.trim()) setImpact(detail.text);
      else {
        const next = { id: crypto.randomUUID(), text: detail.text };
        drafts.set(key, next);
        setDraft(next);
      }
      window.dispatchEvent(new Event("flowfield:show-coordinator"));
    }
    window.addEventListener("flowfield:discuss-document", discuss);
    return () =>
      window.removeEventListener("flowfield:discuss-document", discuss);
  }, [workspaceId, key, drafts]);
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
    try {
      const contextKey = JSON.stringify([
        viewPath,
        { ...adjacent?.view, document_revision: undefined },
        taskContext?.project_id,
        taskContext?.task_id,
        taskContext?.result_id,
      ]);
      const receipt = {
        ...draft,
        id: draft.contextKey === contextKey ? draft.id : crypto.randomUUID(),
        contextKey,
        context: draft.contextKey === contextKey ? draft.context : adjacent!,
      };
      drafts.set(key, receipt);
      setDraft(receipt);
      await request(`${base}/messages`, "POST", {
        id: receipt.id,
        text: receipt.text,
        view_context: receipt.context?.view,
        task_context: receipt.context?.task,
      });
      update("");
      setTick((n) => n + 1);
    } catch (e) {
      // A rejected stale selection has no effects. Other uncertain failures retain
      // the original receipt and context, even if live task revisions advance.
      if (
        e instanceof RequestError &&
        (e.code === "task_context_changed" || e.code === "view_context_changed")
      )
        update(draft.text);
      reportError(e, "Could not send message");
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
    try {
      await request(`${base}/turns/${turn.id}/${operation}`, "POST");
    } catch (e) {
      reportError(e, "Could not update coordinator session");
    } finally {
      setBusy(false);
      setTick((n) => n + 1);
    }
  }
  async function older() {
    if (!cursor || loadingEarlier) return;
    setLoadingEarlier(true);
    scroll.readingEarlier();
    try {
      const result = await request<Page>(`${base}?before=${cursor}`);
      setHistory((previous) => ({
        ...previous,
        items: mergeTurns(result.items, previous.items),
      }));
      setBefore(result.next_before);
    } catch (e) {
      reportError(e, "Could not load earlier messages");
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
                      taskHref(
                        turn.task_context.project_id,
                        turn.task_context,
                      ) +
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
                <CoordinatorActivity
                  items={turn.activity.items}
                  finished={
                    !["starting", "running", "stopping", "uncertain"].includes(
                      turn.status,
                    )
                  }
                />
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
                      workspaceId={workspaceId}
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
            workspaceId={workspaceId}
            role="coordinator"
            onPendingTurns={setPendingPermissions}
            refresh={`${refresh}:${tick}`}
          />
        </div>
      </div>
      <div className="coordinator-composer content-stack">
        {resource.error && (
          <Button variant="link" onClick={() => setTick((n) => n + 1)}>
            Retry loading messages
          </Button>
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

        {impact && (
          <div className="content-stack impact-draft">
            <p>
              Keep your current message and add the document discussion, or
              replace it.
            </p>
            <div className="actions">
              <Button
                size="sm"
                onClick={() => {
                  update(draft.text + "\n\n" + impact);
                  setImpact(null);
                }}
              >
                Add to message
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  update(impact);
                  setImpact(null);
                }}
              >
                Replace message
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setImpact(null)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
        <Composer
          workspaceId={workspaceId}
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
                workspaceId={workspaceId}
                path={`workspaces/${workspaceId}/coordinator-settings`}
                refresh={refresh}
                coordinator
                coordinatorActive={!!active}
                open={
                  controlsActive && (settingsOpen ?? (!choice && !taskContext))
                }
                autoOpened={
                  settingsOpen === undefined && !choice && !taskContext
                }
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
