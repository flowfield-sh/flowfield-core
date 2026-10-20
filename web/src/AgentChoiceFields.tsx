import { useEffect } from "react";
import type { components } from "./api-schema";
import { AgentModelFields } from "./AgentSettings";
import { ContentStack } from "./DetailLayout";
import {
  HarnessModelSource,
  modelSupports,
  useHarnessModels,
} from "./HarnessModels";

type Choice = components["schemas"]["AgentChoice-Output"];

/** Unsaved role selection for workspace setup, sharing the settings controls. */
export function AgentChoiceFields({
  label,
  description,
  showFast = false,
  value,
  change,
  onReady,
}: {
  label: string;
  description: string;
  showFast?: boolean;
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
      <ContentStack space="section">
        <p>{description}</p>
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
          fast={value?.fast ?? false}
          showFast={showFast}
          models={source.models}
          loading={source.loading}
          change={(model, effort, mode, fast) => {
            if (source.kind)
              change({
                harness: source.kind,
                model,
                effort: effort || null,
                mode: mode || null,
                fast,
              });
          }}
        />
      </ContentStack>
    </fieldset>
  );
}
