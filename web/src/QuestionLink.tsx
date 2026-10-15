import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { useContext } from "react";
import { WorkspaceLink } from "./WorkspaceLink";
import { ResourceNavigation } from "./WorkspaceTabs";
import { TooltipLink } from "./TooltipLink";
import { followLink, questionOverlayHref } from "./navigation";

/** Related input uses the current resource tab and preserves native link behavior. */
export function QuestionLink({
  id,
  children,
  tooltip,
  className = "compact-control",
}: {
  id: string;
  children: ReactNode;
  tooltip?: string;
  className?: string;
}) {
  const location = useLocation();
  const navigate = useContext(ResourceNavigation);
  const href = questionOverlayHref(location.pathname, id);
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
