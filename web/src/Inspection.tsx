import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "./ConfirmButton";
import { StatusNotice } from "./StatusNotice";
import { Disclosure } from "./DetailLayout";
import { ResourceRetry } from "./ResourceRetry";
import { WorkspaceLink } from "./WorkspaceLink";
import { reportError } from "./requestFeedback";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Copy = components["schemas"]["Inspection"];
type Version = components["schemas"]["ResultVersion"];

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
  const instructions = useResource<components["schemas"]["InspectionSettings"]>(
    repositoryId ? `projects/${repositoryId}/inspection/settings` : null,
    refresh,
  );
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
    (state === "ready" || state === "stopped") && value?.runtime_prepared;
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
  const titles: Record<string, string> = {
    cold: "Run result",
    preparing: "Preparing result",
    ready: "Result ready to run",
    starting: "Starting result",
    running: "Result running",
    stopping: "Stopping result",
    stopped: "Result stopped",
    failed: "Result could not run",
    uncertain: "Runtime needs attention",
  };
  return (
    <StatusNotice
      title={titles[state]}
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
            instructions.data?.run_command &&
            !active &&
            state !== "uncertain" && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act(
                    prepared &&
                      !value?.instructions_changed &&
                      !value?.locally_changed
                      ? "run"
                      : "prepare",
                    !!value?.locally_changed ||
                      !!value?.instructions_changed ||
                      !!value?.problem,
                  )
                }
              >
                {prepared &&
                !value?.instructions_changed &&
                !value?.locally_changed
                  ? "Run result"
                  : value
                    ? "Prepare again"
                    : "Prepare"}
              </Button>
            )}
          {state === "running" && value?.url && (
            <Button size="sm" variant="outline" asChild>
              <a href={value.url} target="_blank" rel="noreferrer">
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
          {eligible &&
            instructions.data &&
            !instructions.data.run_command &&
            !instructions.data.no_preview_reason && (
              <Button size="sm" variant="outline" asChild>
                <WorkspaceLink
                  to={`/projects/${repositoryId}/edit/integration`}
                >
                  Configure preview
                </WorkspaceLink>
              </Button>
            )}
        </>
      }
    >
      <ResourceRetry resources={[resource, instructions]} />
      {instructions.data?.no_preview_reason && (
        <p>{instructions.data.no_preview_reason}</p>
      )}
      {value?.runtime_problem && <p>{value.runtime_problem}</p>}
      {value?.problem && <p>{value.problem}</p>}
      {value?.locally_changed && (
        <p>
          This copy has local edits. Preparing again preserves them in the
          previous copy.
        </p>
      )}
      {value?.instructions_changed && (
        <p>Run settings changed. Prepare again to use them.</p>
      )}
      {!eligible && (
        <p>
          This result is retained for inspection. Run the current ready result.
        </p>
      )}
      {value?.runtime_output && (
        <Disclosure summary="Runtime output">
          {value.runtime_truncated && <p>Earlier output omitted.</p>}
          {value.runtime_log_truncated && (
            <p>The retained log reached its size limit.</p>
          )}
          <a href={`/api/${path}/inspections/${value.id}/runtime/log`} download>
            Download runtime log
          </a>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">
            {value.runtime_output}
          </pre>
        </Disclosure>
      )}
    </StatusNotice>
  );
}
