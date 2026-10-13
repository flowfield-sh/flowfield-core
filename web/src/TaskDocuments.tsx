import { Popover } from "radix-ui";
import type { components } from "./api-schema";
import { ContentStack } from "./DetailLayout";
import { WorkspaceLink } from "./WorkspaceLink";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";

export function TaskDocuments({
  documents,
}: {
  documents: components["schemas"]["DocumentSnapshot"][];
}) {
  if (!documents.length) return null;
  return (
    <Popover.Root>
      <Popover.Trigger className="reference-link">
        {documents.length} {documents.length === 1 ? "document" : "documents"}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={16}
          className="composer-settings"
          aria-label="Task documents"
        >
          <ContentStack>
            {documents.map((item) => (
              <Tooltip key={item.id}>
                <TooltipTrigger asChild>
                  <WorkspaceLink
                    to={`/workspaces/${item.workspace_id}/documents/${item.id}/revisions/${item.revision}`}
                  >
                    {item.title}
                  </WorkspaceLink>
                </TooltipTrigger>
                <TooltipContent>
                  Revision {item.revision}, saved when this task was prepared.
                </TooltipContent>
              </Tooltip>
            ))}
          </ContentStack>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
