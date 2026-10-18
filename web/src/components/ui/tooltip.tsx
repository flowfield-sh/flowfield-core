"use client";

import * as React from "react";
import { cn } from "cn";
import { Tooltip as TooltipPrimitive } from "radix-ui";

const TooltipOptions = React.createContext<
  Omit<React.ComponentProps<typeof TooltipPrimitive.Provider>, "children">
>({ delayDuration: 0 });

function TooltipProvider({
  delayDuration = 0,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Provider>) {
  return (
    <TooltipOptions.Provider value={{ delayDuration, ...props }}>
      {children}
    </TooltipOptions.Provider>
  );
}

function Tooltip({
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  const options = React.useContext(TooltipOptions);
  // Keep pointer-transit suppression local to each trigger. A neighboring
  // tooltip can open immediately while content remains hoverable and delayed
  // tooltips retain their configured delay.
  return (
    <TooltipPrimitive.Provider {...options}>
      <TooltipPrimitive.Root data-slot="tooltip" {...props} />
    </TooltipPrimitive.Provider>
  );
}

function TooltipTrigger({
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Trigger>) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />;
}

function TooltipContent({
  className,
  sideOffset = 0,
  collisionPadding = 8,
  children,
  hidden,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  // An invisible content element still participates in Radix's pointer grace area.
  // Omit it so a suppressed tooltip cannot prevent the next trigger from opening.
  if (hidden) return null;
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(
          "z-50 w-fit max-w-[min(20rem,calc(100vw-16px))] wrap-anywhere origin-(--radix-tooltip-content-transform-origin) animate-in rounded-md bg-foreground px-3 py-1.5 text-xs text-balance text-background fade-in-0 zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
          className,
        )}
        {...props}
      >
        {children}
        <TooltipPrimitive.Arrow className="z-50 size-2.5 translate-y-[calc(-50%_-_2px)] rotate-45 rounded-[2px] bg-foreground fill-foreground" />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
