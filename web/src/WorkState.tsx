import type { components } from "./api-schema";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type State = components["schemas"]["WorkState"];
export function WorkState({
  state,
  indicatorOnly = false,
  decorative = false,
}: {
  state: State | null | undefined;
  indicatorOnly?: boolean;
  decorative?: boolean;
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
      <span className="work-state-dot" aria-hidden="true" />
      {!indicatorOnly && state.label}
    </span>
  );
  if (indicatorOnly && !decorative)
    return (
      <Tooltip>
        <TooltipTrigger asChild>{content}</TooltipTrigger>
        <TooltipContent>{state.label}</TooltipContent>
      </Tooltip>
    );
  return content;
}
