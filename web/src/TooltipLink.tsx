import { ScopedAnchor } from "./WorkspaceLink";
import type { AnchorHTMLAttributes } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function TooltipLink({
  tooltip,
  children,
  className = "",
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { tooltip: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <ScopedAnchor {...props} className={`inline-flex w-fit ${className}`}>
          {children}
        </ScopedAnchor>
      </TooltipTrigger>
      <TooltipContent collisionPadding={8} className="max-w-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}
