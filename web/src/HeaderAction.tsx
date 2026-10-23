import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { ActionTooltip } from "./ActionTooltip";

/** Compact title actions share their border, accessible name and tooltip. */
export function HeaderAction({
  label,
  tooltip = label,
  ...props
}: Omit<ComponentProps<typeof Button>, "size" | "variant"> & {
  label: string;
  tooltip?: string;
}) {
  return (
    <ActionTooltip label={tooltip} disabled={props.disabled}>
      <Button {...props} size="icon-sm" variant="outline" aria-label={label} />
    </ActionTooltip>
  );
}
