import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useLabels } from "./labelContext";
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
