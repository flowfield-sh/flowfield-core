import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkspaceLink } from "./WorkspaceLink";
import { workspaceHref } from "./navigation";
import type { Milestone } from "./workspace";

export function MilestoneBadge({
  milestone,
  linked = true,
  preparedRevision,
  changed = false,
}: {
  milestone?: Pick<Milestone, "workspace_id" | "key" | "title">;
  preparedRevision?: number;
  changed?: boolean;
  projectId?: string;
  linked?: boolean;
}) {
  if (!milestone) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className="milestone-tag bg-background"
          asChild
        >
          {linked ? (
            <WorkspaceLink
              to={`${workspaceHref(milestone.workspace_id)}/milestones/${milestone.key}${preparedRevision ? `/revisions/${preparedRevision}` : ""}`}
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
        {preparedRevision && (
          <div>
            Prepared with revision {preparedRevision}.
            {changed
              ? " A newer milestone revision is available; the coordinator can review its relevance."
              : ""}
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
