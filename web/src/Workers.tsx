import { SettingsCheckbox } from "./SettingsCheckbox";
import { ContentStack } from "./DetailLayout";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { AgentModelFields } from "./AgentSettings";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Pause, Play, Settings2 } from "lucide-react";
import { ActionTooltip } from "./ActionTooltip";
import { useEffect, useState } from "react";
import { WorkspaceLink as Link } from "./WorkspaceLink";
import type { components } from "./api-schema";
import { request } from "./workspace";
import { useResource } from "./useResource";
import { projectHref, workspaceHref } from "./navigation";
import {
  HarnessModelSource,
  RefreshModels,
  modelSupports,
  useHarnessModels,
  type HarnessKind,
} from "./HarnessModels";

type Settings = components["schemas"]["WorkspaceWorkerSettings"] & {
  problem?: string | null;
};

export function QueueControls({
  workspaceId,
  problems = [],
  refresh,
}: {
  workspaceId: string;
  problems?: { name: string; message: string; projectId: string }[];
  refresh: unknown;
}) {
  const path = `workspaces/${workspaceId}`;
  const settingsHref = workspaceHref(workspaceId);
  const resource = useResource<Settings>(`${path}/workers`, refresh);
  const occupancy = useResource<components["schemas"]["WorkerOccupancy"]>(
    `${path}/workers/occupancy`,
    refresh,
  );
  const [busy, setBusy] = useState(false);
  const data = resource.data;
  const problem = data?.problem;
  return (
    <div className="queue-controls" aria-label="Worker queue">
      <ResourceRetry resources={[resource, occupancy]}>
        Retry queue status
      </ResourceRetry>
      {data && (
        <>
          <div className="queue-status-row">
            <div className="queue-status">
              <span>Queue {data.enabled ? "running" : "paused"}</span>
              <span className="detail-metadata">
                {occupancy.data
                  ? `${occupancy.data.active} of ${data.max_parallel} workers active`
                  : "Loading workers…"}
                {!!occupancy.data?.uncertain &&
                  ` · ${occupancy.data.uncertain} uncertain`}
              </span>
            </div>
            <ActionTooltip label={data.enabled ? "Pause queue" : "Run queue"}>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={data.enabled ? "Pause queue" : "Run queue"}
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const value = await request<Settings>(
                      `${path}/queue`,
                      "POST",
                      {
                        expected_revision: data.revision,
                        enabled: !data.enabled,
                      },
                    );
                    resource.invalidate();
                    resource.setData(value);
                    toast.success(
                      value.enabled
                        ? "Worker queue enabled."
                        : "Worker queue paused.",
                    );
                  } catch (e) {
                    reportError(
                      e,
                      data.enabled
                        ? "Queue could not pause"
                        : "Queue could not start",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {data.enabled ? <Pause /> : <Play />}
              </Button>
            </ActionTooltip>
            <ActionTooltip label="Worker settings">
              <Button size="icon-sm" variant="ghost" asChild>
                <Link
                  to={settingsHref + "/edit/workers"}
                  aria-label="Worker settings"
                >
                  <Settings2 />
                </Link>
              </Button>
            </ActionTooltip>
          </div>
        </>
      )}
      {problem && (
        <Alert>
          <AlertDescription>
            <ContentStack>
              <p>{problem}</p>
              <Link className="text-link" to={settingsHref + "/edit/workers"}>
                Worker settings
              </Link>
            </ContentStack>
          </AlertDescription>
        </Alert>
      )}
      {problems.slice(0, 3).map((item) => (
        <Alert key={item.projectId}>
          <AlertDescription>
            <ContentStack>
              <p>
                <strong>{item.name}</strong>: {item.message}
              </p>
              <Link
                className="text-link"
                to={projectHref(item.projectId) + "/edit/integration"}
              >
                Project setup
              </Link>
            </ContentStack>
          </AlertDescription>
        </Alert>
      ))}
      {problems.length > 3 && workspaceId && (
        <Link
          className="text-link"
          to={workspaceHref(workspaceId) + "/projects"}
        >
          {problems.length - 3} more projects need attention
        </Link>
      )}
    </div>
  );
}

export function WorkerSettings({
  workspaceId,
  onDirty,
  refresh,
  active = true,
}: {
  refresh: unknown;
  workspaceId: string;
  onDirty: (value: boolean) => void;
  active?: boolean;
}) {
  const path = `workspaces/${workspaceId}/workers`;
  const resource = useResource<Settings>(path, refresh);
  const [draft, setDraft] = useState<{
    harness: HarnessKind;
    model: string;
    effort: string;
    mode: string;
    fast: boolean;
    cap: number;
    revision: number;
  } | null>(null);
  const source = useHarnessModels(
    { workspaceId },
    draft?.harness ?? resource.data?.selection?.harness,
    refresh,
    active && !!resource.data,
  );
  const { catalog, models } = source;
  const harness = source.kind;
  const model = draft?.model ?? resource.data?.selection?.model ?? "";
  const effort = draft?.effort ?? resource.data?.selection?.effort ?? "";
  const mode = draft?.mode ?? resource.data?.selection?.mode ?? "";
  const fast = draft?.fast ?? resource.data?.selection?.fast ?? false;
  const cap = draft?.cap ?? resource.data?.max_parallel ?? 1;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty =
    !!draft &&
    !!resource.data &&
    (harness !== resource.data.selection?.harness ||
      model !== (resource.data.selection?.model ?? "") ||
      effort !== (resource.data.selection?.effort ?? "") ||
      mode !== (resource.data.selection?.mode ?? "") ||
      fast !== (resource.data.selection?.fast ?? false) ||
      cap !== resource.data.max_parallel);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  const stale = !!(
    draft &&
    resource.data &&
    draft.revision !== resource.data.revision
  );
  return (
    <section
      className="worker-settings content-stack"
      data-space="section"
      aria-label="Worker settings"
    >
      <p>
        Choose the default model for task workers. Tasks can override it;
        running attempts keep their settings.
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!resource.data || !harness) return;
          setBusy(true);
          setError("");
          try {
            const updated = await request<Settings>(
              path,
              "PUT",
              {
                expected_revision: draft?.revision ?? resource.data.revision,
                selection: {
                  harness,
                  model,
                  effort: effort || null,
                  mode: mode || null,
                  fast,
                },
                max_parallel: cap,
              },
              undefined,
              180000,
            );
            resource.invalidate();
            resource.setData(updated);
            setDraft(null);
            toast.success("Worker settings saved.");
          } catch (e) {
            setError((e as Error).message);
            reportError(e, "Could not save worker settings");
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset
          disabled={busy || !resource.data}
          className="content-stack"
          data-space="section"
        >
          <HarnessModelSource
            source={source}
            change={(harness) =>
              setDraft({
                harness,
                model: "",
                effort: "",
                mode: "",
                fast: false,
                cap,
                revision: draft?.revision ?? resource.data!.revision,
              })
            }
          />
          <AgentModelFields
            model={model}
            effort={effort}
            mode={mode}
            fast={fast}
            showFast
            models={models}
            loading={source.loading || !resource.data}
            change={(model, effort, mode, fast) =>
              setDraft({
                harness: harness!,
                model,
                effort,
                mode,
                fast,
                cap,
                revision: draft?.revision ?? resource.data!.revision,
              })
            }
          />
          <Label className="field block">
            Maximum parallel workers
            <Input
              type="number"
              min={1}
              max={16}
              value={cap}
              onChange={(event) =>
                setDraft({
                  harness: harness!,
                  model,
                  effort,
                  mode,
                  fast,
                  cap: Number(event.target.value),
                  revision: draft?.revision ?? resource.data!.revision,
                })
              }
            />
          </Label>
          <div className="actions editor-actions">
            <Button
              size="sm"
              aria-label="Save worker settings"
              disabled={
                !dirty ||
                catalog.loading ||
                stale ||
                !harness ||
                !source.host?.selectable ||
                !modelSupports(
                  {
                    harness: harness!,
                    model,
                    effort: effort || null,
                    mode: mode || null,
                    fast,
                  },
                  models,
                )
              }
            >
              Save
            </Button>
            <RefreshModels source={source} />
            {dirty && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  setDraft(null);
                  setError("");
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        </fieldset>
      </form>
      {(error || resource.error || stale) && (
        <>
          {stale && (
            <Alert variant="destructive">
              <AlertDescription>
                Settings changed elsewhere. Load the latest settings before
                saving.
              </AlertDescription>
            </Alert>
          )}
          <Button
            size="sm"
            variant="outline"
            className="quiet"
            disabled={busy}
            onClick={async () => {
              if (
                dirty &&
                !window.confirm(
                  "Discard your worker setting edits and load the saved settings?",
                )
              )
                return;
              try {
                resource.invalidate();
                resource.setData(await request<Settings>(path));
                setDraft(null);
                setError("");
              } catch (e) {
                setError((e as Error).message);
                reportError(e, "Could not reload worker settings");
              }
            }}
          >
            Load latest settings
          </Button>
        </>
      )}
    </section>
  );
}

export function QueueSettings({
  workspaceId,
  refresh,
}: {
  workspaceId: string;
  refresh: unknown;
}) {
  const path = `workspaces/${workspaceId}`;
  const settingsHref = workspaceHref(workspaceId);
  const resource = useResource<Settings>(`${path}/workers`, refresh);
  const [busy, setBusy] = useState(false);
  const data = resource.data;
  return (
    <section
      className="content-stack"
      data-space="tight"
      aria-label="Queue settings"
    >
      <SettingsCheckbox
        hint="Starts eligible Up next tasks. Saves immediately and persists after restart; pausing leaves active workers running."
        checked={data?.enabled ?? false}
        disabled={busy || !data}
        onChange={async (event) => {
          if (!data) return;
          const enabled = event.target.checked;
          setBusy(true);
          try {
            const updated = await request<Settings>(`${path}/queue`, "POST", {
              expected_revision: data.revision,
              enabled,
            });
            resource.invalidate();
            resource.setData(updated);
            toast.success(
              updated.enabled
                ? "Worker queue enabled."
                : "Worker queue paused.",
            );
          } catch (failure) {
            reportError(failure, "Could not update worker queue");
          } finally {
            setBusy(false);
          }
        }}
      >
        Run worker queue
      </SettingsCheckbox>
      {!data?.selection && data && (
        <Link to={settingsHref + "/edit/workers"}>
          Choose a worker model first
        </Link>
      )}
      <ResourceRetry resources={[resource]} />
    </section>
  );
}
