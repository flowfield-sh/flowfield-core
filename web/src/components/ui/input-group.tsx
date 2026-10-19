import type { ComponentProps } from "react";
import { cn } from "cn";
import { Input } from "./input";

/** shadcn input-group composition: one focus frame, control, then inline addon. */
export function InputGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      role="group"
      data-slot="input-group"
      className={cn(
        "flex h-9 w-full min-w-0 items-center rounded-md border border-input bg-transparent shadow-xs transition-[color,box-shadow] has-[:focus-visible]:border-ring has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50 dark:bg-input/30",
        className,
      )}
      {...props}
    />
  );
}
export function InputGroupInput({
  className,
  ...props
}: ComponentProps<"input">) {
  return (
    <Input
      data-slot="input-group-control"
      className={cn(
        "h-full flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent",
        className,
      )}
      {...props}
    />
  );
}
export function InputGroupAddon({
  className,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-slot="input-group-addon"
      className={cn("flex shrink-0 items-center pr-1", className)}
      {...props}
    />
  );
}
