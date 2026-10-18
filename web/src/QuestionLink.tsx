import type { ReactNode } from "react";
import { useContext } from "react";
import { WorkspaceLink } from "./WorkspaceLink";
import { ResourceNavigation } from "./WorkspaceTabs";
import { TooltipLink } from "./TooltipLink";
import { followLink } from "./navigation";

/** Related input uses the current resource tab and preserves native link behavior. */
export function QuestionLink({
  id,
  workspaceId,
  children,
  tooltip,
  className = "compact-control",
}: {
  id: string;
  workspaceId: string;
  children: ReactNode;
  tooltip?: string;
  className?: string;
}) {
  const navigate = useContext(ResourceNavigation);
  const href = `/workspaces/${encodeURIComponent(workspaceId)}/requests/question/${encodeURIComponent(id)}`;
  return tooltip ? (
    <TooltipLink
      className={className}
      href={href}
      tooltip={tooltip}
      onClick={(event) => navigate && followLink(event, () => navigate(href))}
    >
      {children}
    </TooltipLink>
  ) : (
    <WorkspaceLink className={className} to={href}>
      {children}
    </WorkspaceLink>
  );
}
