import type { ReactNode } from "react";
import type { components } from "./api-schema";
import { WorkspaceLink } from "./WorkspaceLink";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type State = components["schemas"]["WorkState"];
export function WorkState({
  state,
  linked = false,
  indicatorOnly = false,
  decorative = false,
  showIndicator = true,
  children,
}: {
  state: State | null | undefined;
  linked?: boolean;
  indicatorOnly?: boolean;
  decorative?: boolean;
  showIndicator?: boolean;
  children?: ReactNode;
}) {
  if (!state) return null;
  const content = (
    <span
      className="work-state"
      data-tone={state.tone}
      data-indicator-only={indicatorOnly || undefined}
      role={indicatorOnly && !decorative ? "img" : undefined}
      aria-label={indicatorOnly && !decorative ? state.label : undefined}
      aria-hidden={decorative || undefined}
      tabIndex={indicatorOnly && !decorative ? 0 : undefined}
    >
      {showIndicator && <span className="work-state-dot" aria-hidden="true" />}
      {!indicatorOnly && (children ?? state.label)}
    </span>
  );
  if (indicatorOnly && !decorative)
    return (
      <Tooltip>
        <TooltipTrigger asChild>{content}</TooltipTrigger>
        <TooltipContent>{state.label}</TooltipContent>
      </Tooltip>
    );
  return linked && state.href && !children ? (
    <WorkspaceLink to={state.href} className="work-state-link">
      {content}
    </WorkspaceLink>
  ) : (
    content
  );
}
