import { useNotifications } from "./Notifications";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { AgentModelFields } from "./AgentSettings";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { WorkspaceLink as Link } from "./WorkspaceLink";
import type { components } from "./api-schema";
import { request } from "./workspace";
import { useResource } from "./useResource";
import { projectHref } from "./navigation";

type Settings = components["schemas"]["WorkerSettings"];
type Model = components["schemas"]["ModelOption"];

export function QueueControls({
  projectId,
  refresh,
}: {
  projectId: string;
  refresh: unknown;
}) {
  const { notify } = useNotifications();
  const path = `projects/${projectId}`;
  const resource = useResource<Settings>(`${path}/workers`, refresh);
  const occupancy = useResource<components["schemas"]["WorkerOccupancy"]>(
    `${path}/workers/occupancy`,
    refresh,
  );
  const [busy, setBusy] = useState(false);
  const data = resource.data;
  const problem = data?.problem || resource.error || occupancy.error;
  useEffect(() => {
    if (problem)
      notify(
        {
          key: `queue-status:${projectId}`,
          project_id: projectId,
          title: "Worker queue needs attention",
          message: problem,
          href: projectHref(projectId) + "/edit/workers",
          action: "Worker settings",
        },
        false,
      );
  }, [problem, projectId, notify]);
  return (
    <div className="queue-controls" aria-label="Worker queue">
      {data && (
        <>
          <span className="muted">
            Queue {data.enabled ? "enabled" : "paused"}
            {occupancy.data &&
              ` · ${occupancy.data.active}/${data.max_parallel} active`}
            {!!occupancy.data?.uncertain &&
              ` · ${occupancy.data.uncertain} uncertain`}
          </span>
          <div className="queue-actions">
            <Button
              size="xs"
              variant="outline"
              className="quiet"
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
                } catch (e) {
                  notify({
                    key: `queue-action:${projectId}`,
                    project_id: projectId,
                    title: data.enabled
                      ? "Queue could not pause"
                      : "Queue could not start",
                    message: (e as Error).message,
                    href: projectHref(projectId) + "/edit/workers",
                    action: "Worker settings",
                  });
                } finally {
                  setBusy(false);
                }
              }}
            >
              {data.enabled ? "Pause queue" : "Run queue"}
            </Button>
            {!data.selection && (
              <Button size="xs" variant="outline" asChild>
                <Link to={projectHref(projectId) + "/edit/workers"}>
                  Choose model
                </Link>
              </Button>
            )}
          </div>
        </>
      )}
      {problem && (
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            notify({
              key: `queue-status:${projectId}`,
              project_id: projectId,
              title: "Worker queue needs attention",
              message: problem,
              href: projectHref(projectId) + "/edit/workers",
              action: "Worker settings",
            })
          }
        >
          Queue needs attention
        </Button>
      )}
    </div>
  );
}

export function WorkerSettings({
  projectId,
  onDirty,
  refresh,
}: {
  refresh: unknown;
  projectId: string;
  onDirty: (value: boolean) => void;
}) {
  const path = `projects/${projectId}/workers`;
  const resource = useResource<Settings>(path, refresh);
  const [modelRetry, setModelRetry] = useState(0);
  const catalog = useResource<Model[]>(
    modelRetry ? "worker-models?refresh=true" : "worker-models",
    modelRetry,
    180000,
  );
  const models = catalog.data ?? [];
  const [draft, setDraft] = useState<{
    model: string;
    effort: string;
    mode: string;
    cap: number;
    revision: number;
  } | null>(null);
  const model = draft?.model ?? resource.data?.selection?.model ?? "";
  const effort = draft?.effort ?? resource.data?.selection?.effort ?? "";
  const mode = draft?.mode ?? resource.data?.selection?.mode ?? "";
  const cap = draft?.cap ?? resource.data?.max_parallel ?? 1;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty =
    !!resource.data &&
    (model !== (resource.data.selection?.model ?? "") ||
      effort !== (resource.data.selection?.effort ?? "") ||
      mode !== (resource.data.selection?.mode ?? "") ||
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
        running attempts keep their settings. Workers can download and install
        project dependencies in their separate workspaces.
      </p>
      <p className="detail-metadata">
        Harness: Codex. Native tool decisions never approve code delivery.
      </p>
      {catalog.loading && <p role="status">Loading available models…</p>}
      {!catalog.loading && (catalog.error || !models.length) && (
        <Alert variant={catalog.error ? "destructive" : "default"}>
          <AlertDescription>
            {catalog.error ||
              "No models are available from this Codex installation."}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setModelRetry((value) => value + 1)}
            >
              Reload models
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!resource.data) return;
          setBusy(true);
          setError("");
          try {
            const updated = await request<Settings>(
              path,
              "PUT",
              {
                expected_revision: draft?.revision ?? resource.data.revision,
                selection: {
                  harness: "codex",
                  model,
                  effort: effort || null,
                  mode: mode || null,
                  fast: false,
                },
                max_parallel: cap,
              },
              undefined,
              180000,
            );
            resource.invalidate();
            resource.setData(updated);
            setDraft(null);
          } catch (e) {
            setError((e as Error).message);
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
          <AgentModelFields
            model={model}
            effort={effort}
            mode={mode}
            fast={false}
            models={models}
            loading={catalog.loading || resource.loading}
            change={(model, effort, mode) =>
              setDraft({
                model,
                effort,
                mode,
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
                  model,
                  effort,
                  mode,
                  cap: Number(event.target.value),
                  revision: draft?.revision ?? resource.data!.revision,
                })
              }
            />
          </Label>
          <div className="actions editor-actions">
            <Button
              size="sm"
              disabled={
                !dirty ||
                stale ||
                !models.some(
                  (item) =>
                    item.id === model &&
                    (item.efforts.length
                      ? item.efforts.includes(effort)
                      : !effort),
                )
              }
            >
              Save worker settings
            </Button>
          </div>
        </fieldset>
      </form>
      {(error || resource.error || stale) && (
        <>
          <Alert variant="destructive">
            <AlertDescription>
              {error ||
                resource.error ||
                "Settings changed elsewhere. Load the latest settings before saving."}
            </AlertDescription>
          </Alert>
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
