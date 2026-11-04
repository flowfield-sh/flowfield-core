import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "./ConfirmButton";
import { WorkState } from "./WorkState";
import { StatusNotice } from "./StatusNotice";
import { ResourceRetry } from "./ResourceRetry";
import { Disclosure } from "./DetailLayout";
import { OutputFiles } from "./Files";
import { WorkspaceLink } from "./WorkspaceLink";
import { reportError } from "./requestFeedback";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Copy = components["schemas"]["Inspection"];
type Version = components["schemas"]["ResultVersion"];

export const runtimeTitles: Record<Copy["runtime_state"], string> = {
  cold: "Unprepared",
  preparing: "Preparing",
  ready: "Ready",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  stopped: "Stopped",
  finished: "Finished",
  failed: "Failed",
  uncertain: "Needs attention",
};

export function runtimeStatus(state: Copy["runtime_state"]) {
  const tone: components["schemas"]["WorkState"]["tone"] =
    state === "running" || state === "finished"
      ? "complete"
      : state === "failed" || state === "uncertain"
        ? "attention"
        : state === "preparing" || state === "starting" || state === "stopping"
          ? "active"
          : state === "ready"
            ? "waiting"
            : "idle";
  return { label: runtimeTitles[state], tone };
}

export function RuntimeState({ state }: { state: Copy["runtime_state"] }) {
  return <WorkState state={runtimeStatus(state)} />;
}

export function Inspection({
  projectId,
  repositoryId,
  result,
  refresh,
  current,
}: {
  projectId: string;
  repositoryId?: string;
  result: Version;
  refresh: unknown;
  current: boolean;
}) {
  const path = `workspaces/${projectId}`;
  const resource = useResource<Copy | null>(
    `${path}/inspection?result_id=${encodeURIComponent(result.id)}`,
    refresh,
    10000,
  );
  const [busy, setBusy] = useState(false);
  const value = resource.data;
  const state = value?.runtime_state ?? "cold";
  const active = ["preparing", "starting", "running", "stopping"].includes(
    state,
  );
  const eligible = current && ["ready", "delivered"].includes(result.status);
  const prepared =
    ["ready", "stopped", "finished"].includes(state) && value?.runtime_prepared;
  async function act(
    action: "prepare" | "run" | "stop" | "confirm-stopped",
    newCopy = false,
  ) {
    setBusy(true);
    try {
      const next =
        action === "prepare"
          ? await request<Copy>(`${path}/inspection/runtime`, "POST", {
              result_id: result.id,
              expected_revision: result.revision,
              new_copy: newCopy,
            })
          : await request<Copy>(
              `${path}/inspections/${value!.id}/runtime`,
              "POST",
              { action },
            );
      resource.setData(next);
      resource.invalidate();
    } catch (error) {
      reportError(error, "Could not update result runtime");
    } finally {
      setBusy(false);
    }
  }
  if (!eligible && !value && !resource.error) return null;
  return (
    <StatusNotice
      title={runtimeTitles[state]}
      state={runtimeStatus(state)}
      tone={
        state === "failed" || state === "uncertain"
          ? "attention"
          : state === "preparing" ||
              state === "starting" ||
              state === "stopping"
            ? "progress"
            : "neutral"
      }
      actions={
        <>
          {state === "uncertain" && (
            <ConfirmButton
              size="sm"
              variant="outline"
              disabled={busy}
              title="Confirm runtime stopped?"
              description="Confirm that this result’s processes have stopped. Its files and logs will be preserved."
              binding={value?.id}
              action={() => void act("confirm-stopped")}
            >
              Confirm stopped
            </ConfirmButton>
          )}
          {eligible &&
            result.recipe?.run_command &&
            !active &&
            state !== "uncertain" && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act(
                    prepared && !value?.locally_changed ? "run" : "prepare",
                    !!value?.locally_changed || !!value?.problem,
                  )
                }
              >
                {prepared && !value?.locally_changed
                  ? "Run result"
                  : value
                    ? "Prepare again"
                    : "Prepare"}
              </Button>
            )}
          {state === "running" && value?.url && (
            <Button size="sm" variant="outline" asChild>
              <a
                href={value.resolved_url || value.url}
                target="_blank"
                rel="noreferrer"
              >
                Open result
              </a>
            </Button>
          )}
          {active && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void act("stop")}
            >
              Stop
            </Button>
          )}
          {value && (
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink
                to={`/workspaces/${projectId}/results/${result.id}/runtime`}
              >
                Result output
              </WorkspaceLink>
            </Button>
          )}
          {eligible &&
            !result.recipe?.run_command &&
            !result.recipe?.no_preview_reason && (
              <Button size="sm" variant="outline" asChild>
                <WorkspaceLink to={`/projects/${repositoryId}/edit/execution`}>
                  Project run defaults
                </WorkspaceLink>
              </Button>
            )}
        </>
      }
    >
      <ResourceRetry resources={[resource]} />
      {result.recipe?.no_preview_reason && (
        <p>{result.recipe.no_preview_reason}</p>
      )}
      {!result.recipe?.run_command && !result.recipe?.no_preview_reason && (
        <p>
          No run recipe was saved with this result. Project run defaults apply
          to new results.
        </p>
      )}
      {value?.runtime_problem && <p>{value.runtime_problem}</p>}
      {value?.problem && <p>{value.problem}</p>}
      {value?.artifact_problem && <p>{value.artifact_problem}</p>}
      {!!value?.files.length && (
        <Disclosure summary="Generated files">
          <OutputFiles files={value.files} />
          <p className="detail-metadata">
            From the latest run. Earlier files remain in Files.
          </p>
        </Disclosure>
      )}
      {value?.locally_changed && (
        <p>
          This copy has local edits. Preparing again preserves them in the
          previous copy.
        </p>
      )}
      {!eligible && !current && (
        <p>
          This result is retained for inspection. Run the current ready result.
        </p>
      )}
    </StatusNotice>
  );
}
