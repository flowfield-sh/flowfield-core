import { useEffect, useRef, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ChoiceSelect } from "@/components/ui/choice-select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Popover } from "radix-ui";
import { ChevronDown, Zap } from "lucide-react";
import { ContentStack } from "./DetailLayout";
import {
  HarnessModelSource,
  RefreshModels,
  modelSupports,
  useHarnessModels,
  type HarnessKind,
} from "./HarnessModels";

type Settings = components["schemas"]["AgentSettingsView"];
type Choice = components["schemas"]["AgentChoice-Output"];
type Model = components["schemas"]["ModelOption"];

export function AgentModelFields({
  model,
  effort,
  mode,
  fast,
  models,
  loading,
  known = true,
  change,
  compact = false,
}: {
  model: string;
  effort: string;
  mode: string;
  fast: boolean;
  models: Model[];
  loading: boolean;
  known?: boolean;
  change: (model: string, effort: string, mode: string, fast: boolean) => void;
  compact?: boolean;
}) {
  const selected = models.find((item) => item.id === model);
  return (
    <>
      <Label className="field block">
        Model
        <ChoiceSelect
          compact={compact}
          label="Model"
          value={loading ? "" : model}
          required
          disabled={loading || !models.length}
          onChange={(value) => {
            const next = models.find((item) => item.id === value);
            change(
              value,
              "",
              next?.modes?.some((item) => item.id === mode) ? mode : "",
              false,
            );
          }}
          placeholder={
            loading
              ? "Loading models…"
              : !known
                ? "Loading models…"
                : !models.length
                  ? "Models unavailable"
                  : "Choose a model"
          }
          options={[
            ...(!loading && model && !selected
              ? [
                  {
                    value: model,
                    label: `${model} (${known ? "unavailable" : "saved"})`,
                  },
                ]
              : []),
            ...models.map((item) => ({ value: item.id, label: item.name })),
          ]}
        />
      </Label>
      {(loading ||
        (!model && models.some((item) => item.efforts.length)) ||
        !!selected?.efforts.length) && (
        <Label className="field block">
          Reasoning effort
          <ChoiceSelect
            compact={compact}
            label="Reasoning effort"
            value={loading ? "" : effort}
            required
            disabled={loading || !model || !selected?.efforts.length}
            onChange={(value) => change(model, value, mode, fast)}
            placeholder={
              loading
                ? "Loading efforts…"
                : !model
                  ? "Select a model first"
                  : !selected?.efforts.length
                    ? "Efforts unavailable"
                    : "Choose an effort"
            }
            options={[
              ...(!loading && effort && !selected?.efforts.includes(effort)
                ? [{ value: effort, label: `${effort} (unavailable)` }]
                : []),
              ...(selected?.efforts.map((value) => ({ value, label: value })) ??
                []),
            ]}
          />
        </Label>
      )}
      {!!selected?.modes?.length && (
        <Label className="field block">
          Access mode
          <ChoiceSelect
            compact={compact}
            label="Access mode"
            value={loading ? "" : mode}
            required
            disabled={loading}
            onChange={(value) => change(model, effort, value, fast)}
            placeholder={loading ? "Loading modes…" : "Choose a mode"}
            options={[
              ...(!loading &&
              mode &&
              !selected.modes.some((item) => item.id === mode)
                ? [{ value: mode, label: `${mode} (unavailable)` }]
                : []),
              ...selected.modes.map((item) => ({
                value: item.id,
                label: item.name,
              })),
            ]}
          />
        </Label>
      )}
    </>
  );
}

type AgentSettingsProps = {
  projectId: string;
  path: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
  coordinator?: boolean;
  coordinatorActive?: boolean;
  onReady?: (choice: Choice | null) => void;
  onSaved?: () => void;
  onSaveError?: () => void;
  onCancel?: () => void;
  compact?: boolean;
  open?: boolean;
};

function useAgentSettingsContent({
  projectId,
  path,
  refresh,
  onDirty,
  coordinator = false,
  coordinatorActive = false,
  onReady,
  onSaved,
  onSaveError,
  onCancel,
  compact = false,
  open = true,
}: AgentSettingsProps) {
  const resource = useResource<Settings>(path, refresh);
  const [draft, setDraft] = useState<{
    revision: number;
    selection: Choice | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (!open) {
      setDraft(null);
      setError("");
      setNotice("");
    }
  }
  const data = resource.data;
  const selection = draft ? draft.selection : data?.selection;
  const choice = selection ?? data?.effective?.choice;
  const source = useHarnessModels(
    projectId,
    choice?.harness,
    refresh,
    open && !!data,
  );
  const { catalog } = source;
  const model = choice?.model ?? "";
  const effort = choice?.effort ?? "";
  const mode = choice?.mode ?? "";
  const fast = choice?.fast ?? false;
  const stale = !!(draft && data && draft.revision !== data.revision);
  useEffect(() => {
    onDirty(!!draft || busy);
    return () => onDirty(false);
  }, [draft, busy, onDirty]);
  useEffect(() => {
    const saved = data?.effective?.choice;
    const available =
      !source.loaded ||
      !catalog.data ||
      (!!saved &&
        saved.harness === source.kind &&
        modelSupports(saved, catalog.data));
    onReady?.(!resource.error && available ? (saved ?? null) : null);
  }, [data, resource.error, catalog.data, source.loaded, source.kind, onReady]);
  function change(model: string, effort: string, mode: string, fast: boolean) {
    if (data)
      setDraft({
        revision: draft?.revision ?? data.revision,
        selection: {
          harness: source.kind!,
          model,
          effort: effort || null,
          mode: mode || null,
          fast,
        },
      });
  }
  function changeHarness(harness: HarnessKind) {
    if (data)
      setDraft({
        revision: draft?.revision ?? data.revision,
        selection: {
          harness,
          model: "",
          effort: null,
          mode: null,
          fast: false,
        },
      });
  }
  async function save(reset = false, next = selection) {
    if (!data) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await request<Settings>(
        path,
        "PUT",
        {
          expected_revision: draft?.revision ?? data.revision,
          selection: reset ? null : next,
        },
        undefined,
        180000,
      );
      resource.invalidate();
      resource.setData(updated);
      setDraft(null);
      setNotice(reset ? "Using project defaults." : "Settings saved.");
      onSaved?.();
    } catch (error) {
      setError((error as Error).message);
      onSaveError?.();
    } finally {
      setBusy(false);
    }
  }
  const fields = (
    <ContentStack space="section">
      <p className="detail-metadata">
        {coordinatorActive
          ? "Stop or finish this message to switch harnesses. Other settings apply to the next message."
          : coordinator
            ? "Applies to your next message. Switching harnesses starts a fresh session; saved chat stays available."
            : "Overrides project defaults for the next worker run."}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <fieldset
          disabled={busy}
          className="content-stack"
          data-space={compact ? "content" : "section"}
        >
          <HarnessModelSource
            source={source}
            compact={compact}
            change={changeHarness}
            harnessLocked={coordinatorActive}
          />
          <AgentModelFields
            model={model}
            effort={effort}
            mode={mode}
            fast={fast}
            models={source.models}
            loading={catalog.loading || !data}
            known={!!catalog.data && !catalog.error && source.loaded}
            change={change}
            compact={compact}
          />
          <div className="actions">
            <Button
              size="sm"
              disabled={
                !draft ||
                (coordinatorActive &&
                  selection?.harness !== data?.effective?.choice.harness) ||
                catalog.loading ||
                stale ||
                !selection ||
                !source.host?.selectable ||
                !modelSupports(selection, source.models)
              }
            >
              {busy ? "Saving…" : "Save"}
            </Button>
            <RefreshModels source={source} />
            {(draft || error || resource.error) &&
              (!compact || stale || error || resource.error) && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={async () => {
                    if (!stale && !error && !resource.error) {
                      setDraft(null);
                      return;
                    }
                    if (
                      draft &&
                      !window.confirm(
                        "Discard your edits and load the saved settings?",
                      )
                    )
                      return;
                    try {
                      resource.invalidate();
                      resource.setData(await request<Settings>(path));
                      resource.setError("");
                      setDraft(null);
                      setError("");
                    } catch (error) {
                      setError((error as Error).message);
                    }
                  }}
                >
                  {stale || error || resource.error ? "Reload" : "Cancel"}
                </Button>
              )}
            {compact && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => {
                  setDraft(null);
                  setError("");
                  onCancel?.();
                }}
              >
                Cancel
              </Button>
            )}
          </div>
          {!coordinator && (
            <Button
              type="button"
              size="sm"
              variant="link"
              className="w-fit"
              disabled={stale || (!data?.selection && !draft)}
              onClick={() => void save(true)}
            >
              Use defaults
            </Button>
          )}
        </fieldset>
      </form>
      {(error || resource.error || stale) && (
        <Alert variant="destructive">
          <AlertDescription>
            {error ||
              resource.error ||
              "Settings changed elsewhere. Load the latest settings before saving."}
          </AlertDescription>
        </Alert>
      )}
      {notice && <p role="status">{notice}</p>}
    </ContentStack>
  );
  const saved = data?.effective?.choice;
  const selected =
    saved?.harness === source.kind
      ? source.models.find((item) => item.id === saved?.model)
      : undefined;
  const fastControl =
    saved && (selected?.fast || saved.fast) ? (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            size="icon-sm"
            variant={saved.fast ? "secondary" : "ghost"}
            aria-label="Fast mode"
            aria-pressed={saved.fast ?? false}
            disabled={
              !selected?.fast ||
              !!draft ||
              busy ||
              catalog.loading ||
              resource.loading ||
              !!resource.error
            }
            onClick={() => void save(false, { ...saved, fast: !saved.fast })}
          >
            <Zap size={14} />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Fast mode {saved.fast ? "on" : "off"}.{" "}
          {selected?.fast
            ? selected.fast_description || "Faster responses, increased usage."
            : "Open model settings to verify Fast support."}
        </TooltipContent>
      </Tooltip>
    ) : null;
  return { fields, fastControl };
}

export function AgentSettingsControl({
  open,
  onOpenChange,
  label,
  autoOpened = false,
  ...props
}: AgentSettingsProps & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string;
  autoOpened?: boolean;
}) {
  const { fields, fastControl } = useAgentSettingsContent({
    ...props,
    compact: true,
    open,
    onSaved: () => onOpenChange(false),
    onCancel: () => onOpenChange(false),
    onSaveError: () => onOpenChange(true),
  });
  const automaticallyOpened = useRef(false);
  return (
    <>
      <Popover.Root open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="composer-model"
            title={label}
          >
            <span>{label}</span>
            <ChevronDown size={12} />
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            side="top"
            align="start"
            sideOffset={8}
            className="composer-settings"
            onOpenAutoFocus={(event) => {
              automaticallyOpened.current = autoOpened;
              if (autoOpened) event.preventDefault();
            }}
            onCloseAutoFocus={(event) => {
              if (automaticallyOpened.current) event.preventDefault();
            }}
            onFocusOutside={(event) => {
              // A newly activated workspace tab can receive focus after its popup mounts.
              if (autoOpened) event.preventDefault();
            }}
            aria-label={
              props.coordinator
                ? "Coordinator model settings"
                : "Worker model settings"
            }
          >
            {fields}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {fastControl}
    </>
  );
}
