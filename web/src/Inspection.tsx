import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { useState, useRef, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupInput,
  InputGroupAddon,
} from "@/components/ui/input-group";
import { Copy } from "lucide-react";
import { ActionTooltip } from "./ActionTooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ContentStack } from "./DetailLayout";
import { WorkspaceLink } from "./WorkspaceLink";
import { useResource } from "./useResource";
import { request, RequestError } from "./workspace";
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
  const path = `workspaces/${projectId}`;
  const [identity, setIdentity] = useState<string | null>(null);
  const [saved, setSaved] = useState<Copy | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [retryTick, setRetryTick] = useState(0);
  const handledRetry = useRef(0);
  const retryCopy = useRef(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, []);
  const preparing = useRef(false);
  const resource = useResource<Copy>(
    identity ? `${path}/inspections/${identity}` : null,
    refresh,
  );
  const value = resource.data?.id === identity ? resource.data : saved;
  const invalidate = resource.invalidate;
  const prepare = useCallback(
    (newCopy = false) => {
      if (preparing.current) return;
      preparing.current = true;
      retryCopy.current = newCopy;
      // Reopening a result reads its saved copy. Approval/status updates must
      // not recreate it or silently adopt newer project launch settings.
      return (
        newCopy
          ? Promise.resolve(null)
          : request<Copy | null>(
              `${path}/inspection?result_id=${encodeURIComponent(result.id)}`,
            )
      )
        .then(
          (existing) =>
            existing ??
            request<Copy>(`${path}/inspection`, "POST", {
              result_id: result.id,
              expected_revision: result.revision,
              new_copy: newCopy,
            }),
        )
        .then((next) => {
          if (!mounted.current) return;
          setWaiting(false);
          setSaved(next);
          setIdentity(next.id);
          invalidate();
        })
        .catch((e) => {
          if (!mounted.current) return;
          if (e instanceof RequestError && e.code === "integration_busy") {
            setWaiting(true);
            retryTimer.current = setTimeout(() => {
              if (mounted.current) setRetryTick((tick) => tick + 1);
            }, 2000);
            return;
          }
          setWaiting(false);
          setError((e as Error).message);
          reportError(e, "Could not prepare inspection");
        })
        .finally(() => {
          preparing.current = false;
          if (mounted.current) setBusy(false);
        });
    },
    [path, result.id, result.revision, invalidate],
  );
  useEffect(() => {
    if (!value && !error) void prepare();
  }, [prepare, value, error]);
  useEffect(() => {
    if (retryTick > handledRetry.current) {
      handledRetry.current = retryTick;
      void prepare(retryCopy.current);
    }
  }, [prepare, retryTick]);
  function retry(newCopy = false) {
    setBusy(true);
    setError("");
    void prepare(newCopy);
  }
  return (
    <ContentStack role="region" aria-label="Run command">
      {waiting ? (
        <p role="status">
          Waiting to prepare this preview while repository work finishes…
        </p>
      ) : (
        busy && <p role="status">Preparing preview…</p>
      )}
      <ResourceRetry resources={[resource]} />
      {error && !value && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => retry()}
        >
          Retry preparation
        </Button>
      )}
      {value && (
        <ContentStack>
          {value.source_changed && (
            <p>This preview belongs to this earlier result.</p>
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
          {value.status === "ready" &&
            !value.problem &&
            (value.command ? (
              <>
                <InputGroup>
                  <InputGroupInput
                    className="font-mono text-xs"
                    aria-label="Terminal commands"
                    readOnly
                    value={value.command}
                    onFocus={(event) => event.currentTarget.select()}
                  />
                  <InputGroupAddon>
                    <ActionTooltip label="Copy commands">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label="Copy commands"
                        onClick={() => {
                          void navigator.clipboard
                            .writeText(value.command)
                            .then(
                              () => toast.info("Commands copied."),
                              () =>
                                toast.error(
                                  "Could not copy. Select the commands and copy them manually.",
                                ),
                            );
                        }}
                      >
                        <Copy size={16} aria-hidden="true" />
                      </Button>
                    </ActionTooltip>
                  </InputGroupAddon>
                </InputGroup>
              </>
            ) : (
              <p>
                Set the project run command in{" "}
                <WorkspaceLink
                  to={`/projects/${value.project_id}/edit/integration`}
                >
                  Integration settings
                </WorkspaceLink>
                , then prepare another copy.
              </p>
            ))}
          {(value.locally_changed ||
            value.instructions_changed ||
            value.problem ||
            value.status !== "ready" ||
            !value.command) && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || waiting}
              onClick={() => retry(true)}
            >
              Prepare another copy
            </Button>
          )}
        </ContentStack>
      )}
    </ContentStack>
  );
}
