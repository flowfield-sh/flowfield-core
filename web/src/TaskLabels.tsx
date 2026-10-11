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
import { useLabels, type WorkspaceLabel } from "./labelContext";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ContentStack } from "./DetailLayout";

const colors: Record<WorkspaceLabel["color"], string> = {
  blue: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200",
  orange:
    "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-800 dark:bg-orange-950 dark:text-orange-200",
  slate:
    "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
  violet:
    "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200",
  green:
    "border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-200",
  red: "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200",
  pink: "border-pink-200 bg-pink-50 text-pink-700 dark:border-pink-800 dark:bg-pink-950 dark:text-pink-200",
  yellow:
    "border-yellow-200 bg-yellow-50 text-yellow-800 dark:border-yellow-800 dark:bg-yellow-950 dark:text-yellow-200",
};
export function TaskLabels({ ids }: { ids: string[] }) {
  const labels = useLabels();
  return ids.map((id) => {
    const value = labels.find((item) => item.id === id);
    return (
      <Tooltip key={id}>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={`type-label ${colors[value?.color ?? "slate"]}`}
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
