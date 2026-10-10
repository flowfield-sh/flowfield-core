import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { WorkspaceLink } from "./WorkspaceLink";
import { ContentStack } from "./DetailLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { HarnessSelect } from "./HarnessSelect";
import "./harness-settings.css";

type Status = components["schemas"]["HarnessStatus"];
export type HarnessKind = Status["registration"]["harness"];
type Choice = components["schemas"]["AgentChoice-Output"];
type Model = components["schemas"]["ModelOption"];

export const harnessNames: Record<HarnessKind, string> = {
  codex: "Codex",
  "claude-code": "Claude Code",
  pi: "Pi",
};

export function choiceLabel(choice: Choice) {
  return [harnessNames[choice.harness], choice.model, choice.effort]
    .filter(Boolean)
    .join(" · ");
}

export function modelSupports(choice: Choice, models: Model[]) {
  return models.some(
    (item) =>
      item.id === choice.model &&
      (item.efforts.length
        ? !!choice.effort && item.efforts.includes(choice.effort)
        : !choice.effort) &&
      (item.modes?.length
        ? item.modes.some((mode) => mode.id === choice.mode)
        : !choice.mode) &&
      (!choice.fast || item.fast),
  );
}

// Discovery is model-free: configured views preload; other selectors load on open.
// The service owns coalescing, cleanup and bounded caches; this hook retains one view.
export function useHarnessModels(
  projectId: string | undefined,
  requested: HarnessKind | undefined,
  refresh: unknown,
  open = true,
  projectPath?: string,
) {
  const [connection, setConnection] = useState(0);
  useEffect(() => {
    const reconnect = () => setConnection((value) => value + 1);
    window.addEventListener("flowfield:reconnected", reconnect);
    return () => window.removeEventListener("flowfield:reconnected", reconnect);
  }, []);
  const hosts = useResource<Status[]>("harnesses", refresh);
  const kind =
    requested ??
    hosts.data?.find((item) => item.selectable)?.registration.harness;
  const host = hosts.data?.find((item) => item.registration.harness === kind);
  const key = JSON.stringify([projectId, projectPath, kind, host?.launch]);
  const [load, setLoad] = useState<{ key: string; revision: number } | null>(
    null,
  );
  if (load && (load.key !== key || !open)) setLoad(null);
  const loaded = open && !!kind && !!host?.selectable;
  const query = new URLSearchParams({
    ...(projectId
      ? { project_id: projectId }
      : projectPath
        ? { project_path: projectPath }
        : {}),
    harness: kind ?? "",
    refresh: load?.key === key ? "true" : "false",
  });
  const catalog = useResource<Model[]>(
    loaded && kind ? `worker-models?${query}` : null,
    JSON.stringify([key, load?.revision, connection]),
    180000,
  );
  const [verified, setVerified] = useState<{
    key: string;
    data: Model[] | null;
    error: string;
  } | null>(null);
  if (
    loaded &&
    !catalog.loading &&
    (catalog.data || catalog.error) &&
    (verified?.key !== key ||
      verified.data !== catalog.data ||
      verified.error !== catalog.error)
  ) {
    setVerified({ key, data: catalog.data, error: catalog.error });
  }
  return {
    kind,
    hosts,
    host,
    loaded,
    loading: (hosts.loading && !hosts.data) || catalog.loading,
    catalog,
    models: loaded
      ? !catalog.error
        ? (catalog.data ?? [])
        : []
      : !open && verified?.key === key
        ? verified.error
          ? []
          : (verified.data ?? [])
        : [],
    load: () => setLoad({ key, revision: (load?.revision ?? 0) + 1 }),
  };
}

export function HarnessModelSource({
  source,
  change,
  compact = false,
  disabled = false,
  harnessLocked = false,
}: {
  source: ReturnType<typeof useHarnessModels>;
  change: (kind: HarnessKind) => void;
  compact?: boolean;
  disabled?: boolean;
  harnessLocked?: boolean;
}) {
  const { kind, hosts, host, loaded, catalog } = source;
  const unavailable = !!hosts.error || !host?.selectable;
  return (
    <ContentStack>
      <Label className="field block">
        Harness
        <HarnessSelect
          compact={compact}
          value={kind ?? ""}
          loading={hosts.loading && !hosts.data}
          disabled={disabled || harnessLocked}
          onChange={change}
          options={Object.entries(harnessNames).map(([value, name]) => {
            const status = hosts.data?.find(
              (item) => item.registration.harness === value,
            );
            return {
              value: value as HarnessKind,
              name,
              disabled: !status?.selectable,
            };
          })}
        />
      </Label>
      {hosts.error && (
        <Alert>
          <AlertDescription>{hosts.error}</AlertDescription>
        </Alert>
      )}
      {!hosts.loading && unavailable && (
        <p className="detail-metadata">
          Set up an available harness or resolve its discovery hold.{" "}
          <WorkspaceLink to="/settings/harnesses">
            Harness settings
          </WorkspaceLink>
        </p>
      )}
      {loaded &&
        !catalog.loading &&
        (catalog.error || !source.models.length) && (
          <Alert variant={catalog.error ? "destructive" : "default"}>
            <AlertDescription>
              {catalog.error ||
                (kind === "pi"
                  ? "Pi has no available models. Configure a provider in Pi on the service host, then refresh models."
                  : `No models are available from ${kind ? harnessNames[kind] : "this harness"} for this project. Check its native account and model settings, then refresh models.`)}{" "}
              <WorkspaceLink to="/settings/harnesses">
                Harness settings
              </WorkspaceLink>
            </AlertDescription>
          </Alert>
        )}
    </ContentStack>
  );
}

export function RefreshModels({
  source,
}: {
  source: ReturnType<typeof useHarnessModels>;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={
        source.hosts.loading ||
        !source.host?.selectable ||
        !!source.hosts.error ||
        source.catalog.loading
      }
      onClick={source.load}
    >
      Refresh models
    </Button>
  );
}
