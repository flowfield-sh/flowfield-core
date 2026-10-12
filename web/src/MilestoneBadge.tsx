import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkspaceLink } from "./WorkspaceLink";
import { boardHref } from "./navigation";
import type { Milestone } from "./workspace";

export function MilestoneBadge({
  milestone,
  linked = true,
}: {
  milestone?: Milestone;
  projectId?: string;
  linked?: boolean;
}) {
  if (!milestone) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="milestone-tag" asChild>
          {linked ? (
            <WorkspaceLink
              to={boardHref(milestone.workspace_id, {
                milestone: milestone.id,
              })}
            >
              {milestone.key}
            </WorkspaceLink>
          ) : (
            <span tabIndex={0}>{milestone.key}</span>
          )}
        </Badge>
      </TooltipTrigger>
      <TooltipContent collisionPadding={8} className="max-w-xs">
        {milestone.title}
      </TooltipContent>
    </Tooltip>
  );
}
