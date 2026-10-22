import { useLayoutEffect, useRef, useState, type ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** Dynamic action labels remain fully accessible without growing the action row. */
export function OverflowButton({
  label,
  ...props
}: Omit<ComponentProps<typeof Button>, "children"> & { label: string }) {
  const text = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(false);
  useLayoutEffect(() => {
    const node = text.current;
    if (!node) return;
    const measure = () => setOverflow(node.scrollWidth > node.clientWidth);
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [label]);
  return (
    <Tooltip open={overflow ? undefined : false}>
      <TooltipTrigger asChild>
        <Button {...props} className="max-w-full min-w-0" aria-label={label}>
          <span ref={text} className="block max-w-80 truncate">
            {label}
          </span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
