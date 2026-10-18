import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { ContentStack } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { AgentModelFields } from "./AgentSettings";
import {
  HarnessModelSource,
  RefreshModels,
  modelSupports,
  useHarnessModels,
} from "./HarnessModels";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";

type Settings = components["schemas"]["ReviewerSettingsView"];
const fields = (record?: Settings) => ({
  enabled: record?.enabled ?? null,
  selection: record?.selection ?? null,
});

export function ReviewerSettings({
  workspaceId,
  entity,
  refresh,
  onDirty,
  active = true,
}: {
  workspaceId: string;
  entity?: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
  active?: boolean;
}) {
  const query = new URLSearchParams({
    ...(entity ? { entity } : {}),
  });
  const path = `workspaces/${workspaceId}/reviewer?${query}`;
  const resource = useResource<Settings>(path, refresh);
  const state = useRecordEditor({
    incoming: resource.data ?? undefined,
    recordName: "reviewer settings",
    version: (record) => JSON.stringify([record.revision, record.effective]),
    fields,
    path: () => path,
    onDirty,
    saved: (record: Settings) => {
      resource.setData(record);
    },
  });
  const { values, loaded, change, run, busy, dirty, newer, element } = state;
  const choice = values.selection ?? loaded?.effective.agent?.choice;
  const enabled = values.enabled ?? loaded?.effective.enabled ?? true;
  const source = useHarnessModels(
    { workspaceId },
    choice?.harness,
    refresh,
    active && enabled && !!loaded,
  );
  const setChoice = (
    model: string,
    effort: string,
    mode: string,
    fast: boolean,
  ) =>
    change("selection", {
      harness: source.kind!,
      model,
      effort: effort || null,
      mode: mode || null,
      fast,
    });
  return (
    <section ref={element} aria-label="Reviewer settings">
      <ContentStack space="section">
        <p>
          {entity
            ? "Inherits workspace reviewer settings until overridden."
            : "Reviewers check task results before delivery. Tasks inherit these settings unless overridden."}
        </p>
        <EditorFeedback state={state} />
        {resource.error && (
          <Alert variant="destructive">
            <AlertDescription>{resource.error}</AlertDescription>
          </Alert>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run("PUT", { expected_revision: loaded!.revision, ...values });
          }}
        >
          <fieldset
            disabled={busy || !loaded}
            className="content-stack"
            data-space="section"
          >
            <SettingsCheckbox
              checked={enabled}
              onChange={(event) => change("enabled", event.target.checked)}
            >
              Automatically assess task outcomes
            </SettingsCheckbox>
            {enabled && (
              <>
                <HarnessModelSource
                  source={source}
                  change={(harness) =>
                    change("selection", {
                      harness,
                      model: "",
                      effort: null,
                      mode: null,
                      fast: false,
                    })
                  }
                />
                <AgentModelFields
                  model={choice?.model ?? ""}
                  effort={choice?.effort ?? ""}
                  mode={choice?.mode ?? ""}
                  fast={choice?.fast ?? false}
                  showFast={!!entity}
                  models={source.models}
                  loading={source.loading}
                  change={setChoice}
                />
              </>
            )}
            <div className="actions">
              <Button
                size="sm"
                disabled={
                  !dirty ||
                  newer ||
                  (enabled &&
                    (!choice || !modelSupports(choice, source.models)))
                }
              >
                {busy ? "Saving…" : "Save"}
              </Button>
              {entity && (
                <Button
                  size="sm"
                  type="button"
                  variant="outline"
                  disabled={
                    newer ||
                    (!loaded?.selection && loaded?.enabled == null && !dirty)
                  }
                  onClick={() =>
                    void run("PUT", {
                      expected_revision: loaded!.revision,
                      enabled: null,
                      selection: null,
                    })
                  }
                >
                  Reset
                </Button>
              )}
              {enabled && <RefreshModels source={source} />}
            </div>
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
