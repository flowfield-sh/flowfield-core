import { useId } from "react";
import { Popover } from "radix-ui";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useLabels } from "./labelContext";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ContentStack } from "./DetailLayout";

import { labelColors } from "./labelColors";

export function TaskLabels({ ids }: { ids: string[] }) {
  const labels = useLabels();
  return ids.map((id) => {
    const value = labels.find((item) => item.id === id);
    return (
      <Tooltip key={id}>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={`type-label ${labelColors[value?.color ?? "slate"].className}`}
            tabIndex={0}
          >
            {value?.name ?? id}
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          {value?.deleted_at
            ? "Deleted label. Preserved in task history."
            : value?.guidance || value?.name || id}
        </TooltipContent>
      </Tooltip>
    );
  });
}
export function LabelPicker({
  value,
  change,
}: {
  value: string[];
  change: (ids: string[]) => void;
}) {
  const id = useId();
  const labels = useLabels().filter((item) => !item.deleted_at);
  return (
    <ContentStack space="tight">
      <Label htmlFor={id}>Labels</Label>
      <Popover.Root>
        <Popover.Trigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            size="sm"
            className="w-full justify-between"
            aria-label="Choose labels"
          >
            <span className="truncate">
              {value.length ? `${value.length} selected` : "No labels"}
            </span>
            <ChevronDown size={14} />
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            align="start"
            sideOffset={6}
            collisionPadding={16}
            aria-label="Task labels"
            className="z-50 w-80 max-w-[calc(100vw-2rem)] rounded-md border bg-popover p-3 text-popover-foreground shadow-md"
          >
            <ContentStack className="max-h-80 overflow-y-auto" space="content">
              {labels.map((item) => (
                <SettingsCheckbox
                  key={item.id}
                  checked={value.includes(item.id)}
                  disabled={!value.includes(item.id) && value.length >= 20}
                  hint={item.guidance}
                  onChange={(event) =>
                    change(
                      event.target.checked
                        ? [...value, item.id]
                        : value.filter((id) => id !== item.id),
                    )
                  }
                >
                  {item.name}
                </SettingsCheckbox>
              ))}
              {!labels.length && (
                <p className="detail-metadata">No workspace labels.</p>
              )}
            </ContentStack>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </ContentStack>
  );
}
