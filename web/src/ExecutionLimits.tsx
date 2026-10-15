import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ContentStack, DetailSection } from "./DetailLayout";
import { OverlayFooter } from "./EntityOverlay";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { request, type Task } from "./workspace";
import { useResource } from "./useResource";
import type { components } from "./api-schema";

type Limits = components["schemas"]["TaskLimits"];
type Policy = components["schemas"]["LimitPolicy"];

export function ExecutionLimitResponse({
  projectId,
  task,
  onDirty,
  inbox = false,
}: {
  inbox?: boolean;
  projectId: string;
  task: Task;
  onDirty: (dirty: boolean) => void;
}) {
  const limit = task.execution_limits;
  const [draft, setDraft] = useState<{
    revision: number;
    minutes: number;
    corrections: number;
    note: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedRevision, setSavedRevision] = useState<number | null>(null);
  const id = useId();
  useEffect(() => {
    onDirty(!!draft);
    return () => onDirty(false);
  }, [draft, onDirty]);
  if (
    (!limit?.blocked ||
      (savedRevision !== null && limit.revision <= savedRevision)) &&
    !draft
  )
    return null;
  if (!limit || (!task.execution_limit_actionable && !draft)) return null;
  const value = draft ?? {
    revision: limit.revision,
    minutes: limit.blocked === "active_time" ? 15 : 0,
    corrections: limit.corrections_used >= limit.corrections_allowed ? 1 : 0,
    note: "",
  };
  const stale =
    value.revision !== limit.revision ||
    !limit.blocked ||
    !task.execution_limit_actionable;
  function edit(patch: Partial<typeof value>) {
    setDraft({ ...value, ...patch });
  }
  const context = (
    <ContentStack space="tight">
      {" "}
      <p>
        {limit.blocked === "active_time"
          ? "This task used its active execution allowance."
          : "This task used its follow-up attempts."}{" "}
        Review the preserved work before allowing it to continue.
      </p>
      <p className="detail-metadata">
        {Math.round(limit.active_seconds_used / 6) / 10} of{" "}
        {Math.round(limit.active_seconds_allowed / 6) / 10} active minutes ·{" "}
        {limit.corrections_used} of {limit.corrections_allowed} follow-up
        attempts
      </p>
      {limit.earlier_usage_unknown && (
        <p>
          Earlier usage is incomplete. Any unobserved running interval after a
          service interruption is charged conservatively.
        </p>
      )}
    </ContentStack>
  );
  return (
    <>
      {inbox && (
        <ContentStack>
          <h3 className="detail-title">{task.title}</h3>
          {context}
        </ContentStack>
      )}
      <OverlayFooter>
        <form
          className="task-input content-stack"
          aria-label="Execution allowance"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              const saved = await request<Limits>(
                `projects/${projectId}/tasks/${task.id}/execution-limits`,
                "POST",
                {
                  expected_revision: value.revision,
                  active_seconds: value.minutes * 60,
                  corrections: value.corrections,
                  note: value.note.trim(),
                },
              );
              setSavedRevision(saved.revision);
              setDraft(null);
              onDirty(false);
            } catch (error) {
              reportError(error, "Could not extend execution allowance");
            } finally {
              setBusy(false);
            }
          }}
        >
          {!inbox && context}
          <div className="actions">
            <ContentStack space="tight">
              <Label htmlFor={`${id}-minutes`}>Additional minutes</Label>
              <Input
                id={`${id}-minutes`}
                type="number"
                min={0}
                max={1440}
                value={value.minutes}
                onChange={(e) => edit({ minutes: Number(e.target.value) })}
              />
            </ContentStack>
            <ContentStack space="tight">
              <Label htmlFor={`${id}-corrections`}>Additional follow-ups</Label>
              <Input
                id={`${id}-corrections`}
                type="number"
                min={0}
                max={20}
                value={value.corrections}
                onChange={(e) => edit({ corrections: Number(e.target.value) })}
              />
            </ContentStack>
          </div>
          <div className="task-input-frame">
            <Textarea
              aria-label="Continuation note"
              placeholder="What should the worker do next?"
              required
              value={value.note}
              onChange={(e) => edit({ note: e.target.value })}
            />
          </div>
          <p className="detail-metadata">
            Continues when the task is ready and its queue is enabled. Code
            approval remains separate.
          </p>
          {stale && (
            <p role="status">
              The allowance changed. Your note is retained; discard it to load
              the current allowance.
            </p>
          )}
          <div className="actions">
            <Button
              disabled={
                busy ||
                stale ||
                !value.note.trim() ||
                (!value.minutes && !value.corrections)
              }
            >
              Add allowance and continue
            </Button>
            {draft && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setDraft(null)}
              >
                Discard
              </Button>
            )}
          </div>
        </form>
      </OverlayFooter>
    </>
  );
}

export function ExecutionLimitRequest({
  projectId,
  taskId,
  refresh,
  onDirty,
}: {
  projectId: string;
  taskId: string;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
}) {
  const task = useResource<Task>(
    `projects/${projectId}/view/tasks/${taskId}`,
    refresh,
  );
  return (
    <>
      <ResourceRetry resources={[task]} />
      {task.data && (
        <ExecutionLimitResponse
          projectId={projectId}
          task={task.data}
          onDirty={onDirty}
          inbox
        />
      )}
    </>
  );
}

export function ExecutionLimitSettings({
  workspaceId,
  refresh,
  onDirty,
}: {
  workspaceId: string;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
}) {
  const path = `workspaces/${workspaceId}/execution-limits`;
  const resource = useResource<Policy>(path, refresh);
  const [draft, setDraft] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const id = useId();
  useEffect(() => {
    onDirty(!!draft);
    return () => onDirty(false);
  }, [draft, onDirty]);
  const value = draft ?? resource.data;
  return (
    <DetailSection title="Task execution limits">
      <p>
        New tasks receive this allowance when execution first starts. Answers,
        retries and restarts keep the remaining allowance. Waiting for a human
        does not use active time.
      </p>
      <ResourceRetry resources={[resource]} />
      {value && (
        <form
          className="content-stack"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const saved = await request<Policy>(path, "PUT", {
                expected_revision: value.revision,
                active_seconds: value.active_seconds,
                corrections: value.corrections,
              });
              resource.setData(saved);
              resource.invalidate();
              setDraft(null);
            } catch (error) {
              reportError(error, "Could not save execution limits");
            } finally {
              setBusy(false);
            }
          }}
        >
          <ContentStack space="tight">
            <Label htmlFor={`${id}-time`}>Active minutes per task</Label>
            <Input
              id={`${id}-time`}
              type="number"
              min={1}
              max={1440}
              required
              value={value.active_seconds / 60}
              onChange={(e) =>
                setDraft({
                  ...value,
                  active_seconds: Number(e.target.value) * 60,
                })
              }
            />
          </ContentStack>
          <ContentStack space="tight">
            <Label htmlFor={`${id}-retries`}>Follow-up attempts per task</Label>
            <Input
              id={`${id}-retries`}
              type="number"
              min={0}
              max={20}
              required
              value={value.corrections}
              onChange={(e) =>
                setDraft({ ...value, corrections: Number(e.target.value) })
              }
            />
          </ContentStack>
          <div className="actions">
            <Button disabled={busy || !draft}>Save task limits</Button>
            {draft && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setDraft(null)}
              >
                Cancel
              </Button>
            )}
          </div>
        </form>
      )}
    </DetailSection>
  );
}
