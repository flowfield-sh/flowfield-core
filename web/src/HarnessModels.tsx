import { useState } from "react";
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

// Discovery is model-free and starts only while the selector is visible.
// The service owns coalescing, cleanup and bounded caches; this hook retains one view.
export function useHarnessModels(
  projectId: string,
  requested: HarnessKind | undefined,
  refresh: unknown,
  open = true,
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
  if (load && (load.key !== key || !open)) setLoad(null);
  const loaded = open && !!kind && !!host?.selectable;
  const query = new URLSearchParams({
    project_id: projectId,
    harness: kind ?? "",
    refresh: load?.key === key ? "true" : "false",
  });
  const catalog = useResource<Model[]>(
    loaded && kind ? `worker-models?${query}` : null,
    JSON.stringify([key, load?.revision]),
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
          disabled={disabled || harnessLocked || (hosts.loading && !hosts.data)}
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
                `No models are available from ${kind ? harnessNames[kind] : "this harness"} for this project.`}
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
