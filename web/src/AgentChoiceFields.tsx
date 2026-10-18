import { useEffect } from "react";
import type { components } from "./api-schema";
import { AgentModelFields } from "./AgentSettings";
import { ContentStack } from "./DetailLayout";
import {
  HarnessModelSource,
  RefreshModels,
  modelSupports,
  useHarnessModels,
} from "./HarnessModels";

type Choice = components["schemas"]["AgentChoice-Output"];

/** Unsaved role selection for workspace setup, sharing the settings controls. */
export function AgentChoiceFields({
  label,
  value,
  change,
  onReady,
}: {
  label: string;
  value: Choice | null;
  change: (choice: Choice) => void;
  onReady: (ready: boolean) => void;
}) {
  const source = useHarnessModels(undefined, value?.harness, undefined);
  const ready = !!value && modelSupports(value, source.models);
  useEffect(() => onReady(ready), [ready, onReady]);
  return (
    <fieldset aria-label={label} className="content-stack">
      <legend className="detail-title mb-2">{label}</legend>
      <ContentStack>
        <HarnessModelSource
          source={source}
          change={(harness) =>
            change({
              harness,
              model: "",
              effort: null,
              mode: null,
              fast: false,
            })
          }
        />
        <AgentModelFields
          model={value?.model ?? ""}
          effort={value?.effort ?? ""}
          mode={value?.mode ?? ""}
          fast={false}
          models={source.models}
          loading={source.loading}
          change={(model, effort, mode) => {
            if (source.kind)
              change({
                harness: source.kind,
                model,
                effort: effort || null,
                mode: mode || null,
                fast: false,
              });
          }}
        />
        <div className="actions">
          <RefreshModels source={source} />
        </div>
      </ContentStack>
    </fieldset>
  );
}
