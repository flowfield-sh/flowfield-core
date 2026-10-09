import { useState } from "react";
import { Popover } from "radix-ui";
import { ChevronDown } from "lucide-react";
import type { components } from "./api-schema";
import { AgentModelFields } from "./AgentSettings";
import { ContentStack } from "./DetailLayout";
import {
  HarnessModelSource,
  RefreshModels,
  choiceLabel,
  modelSupports,
  useHarnessModels,
} from "./HarnessModels";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

type Choice = components["schemas"]["AgentChoice-Output"];

/** Draft choices only; Add project owns persistence for both roles. */
export function ProjectAgentChoice({
  path,
  role,
  value,
  capacity,
  change,
  disabled,
}: {
  path: string;
  role: "Coordinator" | "Workers";
  value: Choice | null;
  capacity: number;
  change: (choice: Choice | null, capacity: number) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [cap, setCap] = useState(capacity);
  const source = useHarnessModels(undefined, draft?.harness, 0, open, path);
  const valid =
    !!draft &&
    !source.loading &&
    !!source.host?.selectable &&
    !source.hosts.error &&
    modelSupports(draft, source.models);
  function toggle(next: boolean) {
    setDraft(value);
    setCap(capacity);
    setOpen(next);
  }
  return (
    <div className="project-agent-choice">
      <span>{role}</span>
      <Popover.Root open={open} onOpenChange={toggle}>
        <Popover.Trigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            aria-label={`${role} settings`}
          >
            <span>{value ? choiceLabel(value) : "Choose model"}</span>
            <ChevronDown size={12} />
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            className="composer-settings"
            align="start"
            sideOffset={8}
            aria-label={`${role} settings`}
          >
            <ContentStack>
              <HarnessModelSource
                source={source}
                compact
                change={(harness) =>
                  setDraft({
                    harness,
                    model: "",
                    effort: null,
                    mode: null,
                    fast: false,
                  })
                }
              />
              <AgentModelFields
                compact
                model={draft?.model ?? ""}
                effort={draft?.effort ?? ""}
                mode={draft?.mode ?? ""}
                fast={draft?.fast ?? false}
                models={source.models}
                loading={source.loading}
                change={(model, effort, mode, fast) =>
                  setDraft({
                    harness: source.kind!,
                    model,
                    effort: effort || null,
                    mode: mode || null,
                    fast,
                  })
                }
              />
              {role === "Workers" && (
                <Label className="field block">
                  Parallel workers
                  <Input
                    type="number"
                    min={1}
                    max={16}
                    value={cap || ""}
                    onChange={(event) => setCap(event.target.valueAsNumber)}
                  />
                </Label>
              )}
              <div className="actions">
                <Button
                  type="button"
                  size="sm"
                  disabled={
                    !valid || !Number.isInteger(cap) || cap < 1 || cap > 16
                  }
                  onClick={() => {
                    change(draft, cap);
                    setOpen(false);
                  }}
                >
                  Save
                </Button>
                <RefreshModels source={source} />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => toggle(false)}
                >
                  Cancel
                </Button>
              </div>
              {value && (
                <Button
                  type="button"
                  size="sm"
                  variant="link"
                  className="w-fit"
                  onClick={() => {
                    change(null, 1);
                    setOpen(false);
                  }}
                >
                  Set up later
                </Button>
              )}
            </ContentStack>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
