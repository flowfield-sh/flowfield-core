import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { components } from "./api-schema";

export function ContextRing({
  context,
  active = false,
  inline = false,
}: {
  context?: components["schemas"]["ContextUsage"] | null;
  active?: boolean;
  inline?: boolean;
}) {
  const percent = context
    ? Math.round((context.used / context.size) * 100)
    : null;
  const label =
    percent === null
      ? "Context usage not reported"
      : `${percent}% context used`;
  const ring = (
    <span
      tabIndex={inline ? undefined : 0}
      className="context-ring"
      aria-label={label}
    >
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <circle
          cx="10"
          cy="10"
          r="7"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          opacity="0.18"
        />
        {percent !== null && (
          <circle
            cx="10"
            cy="10"
            r="7"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            pathLength="100"
            strokeDasharray={`${Math.min(100, percent)} 100`}
            transform="rotate(-90 10 10)"
          />
        )}
      </svg>
    </span>
  );
  if (inline)
    return (
      <>
        {ring}
        <span className="detail-metadata">
          {context ? (
            <>
              {!active && "Last reported · "}
              {context.used.toLocaleString()} / {context.size.toLocaleString()}{" "}
              context tokens
            </>
          ) : (
            "Context usage not reported"
          )}
        </span>
      </>
    );
  return (
    <Tooltip>
      <TooltipTrigger asChild>{ring}</TooltipTrigger>
      <TooltipContent>
        {context ? (
          <>
            {active ? "Current turn" : "Last reported"}:{" "}
            {context.used.toLocaleString()} / {context.size.toLocaleString()}{" "}
            tokens ({percent}%).
          </>
        ) : (
          "The agent has not reported context usage."
        )}
      </TooltipContent>
    </Tooltip>
  );
}
