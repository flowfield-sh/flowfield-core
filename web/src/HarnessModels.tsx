import { useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { WorkspaceLink } from "./WorkspaceLink";
import { ContentStack } from "./DetailLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";

type Status = components["schemas"]["HarnessStatus"];
export type HarnessKind = Status["registration"]["harness"];
type Choice = components["schemas"]["AgentChoice-Output"];
type Model = components["schemas"]["ModelOption"];

export const harnessNames: Record<HarnessKind, string> = {
  codex: "Codex",
  "claude-code": "Claude Code",
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

// Detection is model-free. Native sessions start only after a deliberate load.
// The service owns coalescing, cleanup and bounded caches; this hook retains one view.
export function useHarnessModels(
  projectId: string,
  requested: HarnessKind | undefined,
  refresh: unknown,
) {
  const hosts = useResource<Status[]>("harnesses", refresh);
  const kind =
    requested ??
    hosts.data?.find((item) => item.selectable)?.registration.harness;
  const host = hosts.data?.find((item) => item.registration.harness === kind);
  const key = JSON.stringify([projectId, kind, host?.launch]);
  const [load, setLoad] = useState<{ key: string; revision: number } | null>(
    null,
  );
  const loaded = load?.key === key;
  const query = new URLSearchParams({
    project_id: projectId,
    harness: kind ?? "",
    refresh: "true",
  });
  const catalog = useResource<Model[]>(
    loaded && kind ? `worker-models?${query}` : null,
    load?.revision,
    180000,
  );
  return {
    kind,
    hosts,
    host,
    loaded,
    catalog,
    models: loaded && !catalog.error ? (catalog.data ?? []) : [],
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
  const unavailable = !!hosts.error || !host?.selectable || !!host.installing;
  return (
    <ContentStack>
      <Label className="field block">
        Harness
        <NativeSelect
          aria-label="Harness"
          size={compact ? "sm" : "default"}
          value={kind ?? ""}
          disabled={disabled || harnessLocked || hosts.loading}
          onChange={(event) => change(event.target.value as HarnessKind)}
        >
          <option value="" disabled>
            {hosts.loading ? "Loading harnesses…" : "Choose a harness"}
          </option>
          {Object.entries(harnessNames).map(([value, name]) => {
            const status = hosts.data?.find(
              (item) => item.registration.harness === value,
            );
            return (
              <option key={value} value={value} disabled={!status?.selectable}>
                {name}
                {status?.selectable
                  ? ""
                  : status?.problems.includes("adapter_not_available")
                    ? " (in progress)"
                    : " (setup needed)"}
              </option>
            );
          })}
        </NativeSelect>
      </Label>
      {harnessLocked && (
        <p className="detail-metadata">
          Finish or Stop this message before switching harnesses. If cleanup is
          uncertain, confirm the coordinator stopped.
        </p>
      )}
      {hosts.error && (
        <Alert>
          <AlertDescription>{hosts.error}</AlertDescription>
        </Alert>
      )}
      {!hosts.loading && unavailable && (
        <p className="detail-metadata">
          {host?.problems.includes("adapter_not_available")
            ? "This harness's managed integration is in progress."
            : "Set up an available harness or resolve its discovery hold."}{" "}
          <WorkspaceLink to="/settings/harnesses">
            Harness settings
          </WorkspaceLink>
        </p>
      )}
      <p className="detail-metadata">
        Loading models opens a native session for this project. Startup hooks
        can run; no model prompt is sent.
      </p>
      <div className="actions">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled || hosts.loading || unavailable || catalog.loading}
          onClick={source.load}
        >
          {catalog.loading
            ? "Loading models…"
            : loaded
              ? "Reload models"
              : "Load models"}
        </Button>
      </div>
      {loaded &&
        !catalog.loading &&
        (catalog.error || !source.models.length) && (
          <Alert variant={catalog.error ? "destructive" : "default"}>
            <AlertDescription>
              {catalog.error ||
                `No models are available from ${kind ? harnessNames[kind] : "this harness"} for this project.`}
            </AlertDescription>
          </Alert>
        )}
    </ContentStack>
  );
}
