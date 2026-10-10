import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { useState, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { DetailSection, Disclosure } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Copy = components["schemas"]["Inspection"];
type Version = components["schemas"]["ResultVersion"];

export function Inspection({
  projectId,
  result,
  refresh,
}: {
  projectId: string;
  result: Version;
  refresh: unknown;
}) {
  const path = `projects/${projectId}`;
  const [identity, setIdentity] = useState<string | null>(null);
  const [saved, setSaved] = useState<Copy | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preparing = useRef(false);
  const resource = useResource<Copy>(
    identity && open ? `${path}/inspections/${identity}` : null,
    refresh,
  );
  const value = resource.data?.id === identity ? resource.data : saved;
  async function prepare(newCopy = false) {
    if (preparing.current) return;
    preparing.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await request<Copy>(`${path}/inspection`, "POST", {
        result_id: result.id,
        expected_revision: result.revision,
        new_copy: newCopy,
      });
      setSaved(next);
      setIdentity(next.id);
      resource.invalidate();
    } catch (e) {
      setError((e as Error).message);
      reportError(e, "Could not prepare inspection");
    } finally {
      preparing.current = false;
      setBusy(false);
    }
  }
  return (
    <Disclosure
      group
      summary={<>Try result</>}
      role="region"
      aria-label="Try result"
      open={open}
      onToggle={(e) => {
        const expanded = e.currentTarget.open;
        setOpen(expanded);
        if (expanded && !value && !error) void prepare();
      }}
    >
      {busy && <p role="status">Preparing inspection…</p>}
      <ResourceRetry resources={[resource]} />
      {error && !value && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void prepare()}
        >
          Retry preparation
        </Button>
      )}
      {open && value && (
        <DetailSection title={`${value.task_key} · Review ${value.version}`}>
          <p>
            Separate inspection copy · <Timestamp date={value.created_at} />
          </p>
          {value.source_changed && (
            <p>
              A newer result or run exists. This copy contains the selected
              review.
            </p>
          )}
          {value.locally_changed && (
            <p>Approval uses the saved result, excluding edits in this copy.</p>
          )}
          {value.instructions_changed && (
            <p>
              Project run or setup instructions changed. Prepare another copy to
              use them.
            </p>
          )}
          {value.problem && (
            <Alert variant="destructive">
              <AlertDescription>{value.problem}</AlertDescription>
            </Alert>
          )}
          {value.status === "preparing" && (
            <p>Still preparing. Retry if the service was interrupted.</p>
          )}
          {value.workspace && (
            <p>
              Directory: <code>{value.workspace}</code>
            </p>
          )}
          {value.status === "ready" &&
            !value.problem &&
            (value.command ? (
              <>
                <p>
                  Run in your terminal on the service machine. Setup runs first;
                  stop a running application with Ctrl+C.
                </p>
                <p>
                  Run: <code>{value.run_command}</code>
                </p>
                <Disclosure summary={<>Terminal commands</>}>
                  <pre className="evidence-output">{value.command}</pre>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      void navigator.clipboard.writeText(value.command).then(
                        () => toast.info("Commands copied."),
                        () =>
                          toast.error(
                            "Could not copy. Open Terminal commands and copy them manually.",
                          ),
                      );
                    }}
                  >
                    Copy commands
                  </Button>
                </Disclosure>
              </>
            ) : (
              <p>
                Set the project run command in{" "}
                <WorkspaceLink to={`/projects/${projectId}/edit/integration`}>
                  Integration settings
                </WorkspaceLink>
                , then prepare another copy.
              </p>
            ))}
          {(value.locally_changed ||
            value.source_changed ||
            value.instructions_changed ||
            value.problem ||
            value.status !== "ready" ||
            !value.command) && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void prepare(true)}
            >
              Prepare another copy
            </Button>
          )}
        </DetailSection>
      )}
    </Disclosure>
  );
}
