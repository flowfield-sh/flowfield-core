import { useLayoutEffect, useRef, useState } from "react";

/** Keep tab overflow local to the strip, including when a pane is resized. */
export function useTabOverflow(
  content: unknown,
  activeKey: unknown,
  activeSelector: string,
) {
  const strip = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState({ left: false, right: false });
  useLayoutEffect(() => {
    const host = strip.current;
    const list = host?.matches('[role="tablist"]')
      ? host
      : host?.querySelector<HTMLElement>('[role="tablist"]');
    if (!host || !list) return;
    const measure = () => {
      const left = host.scrollLeft > 1;
      const right = host.scrollWidth - host.clientWidth - host.scrollLeft > 1;
      setOverflow((current) =>
        current.left === left && current.right === right
          ? current
          : { left, right },
      );
    };
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY))
        return;
      if (host.scrollWidth <= host.clientWidth) return;
      event.preventDefault();
      const unit =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? host.clientWidth
            : 1;
      host.scrollLeft += event.deltaY * unit;
    };
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    observer.observe(list);
    host.addEventListener("scroll", measure);
    host.addEventListener("wheel", wheel, { passive: false });
    measure();
    return () => {
      observer.disconnect();
      host.removeEventListener("scroll", measure);
      host.removeEventListener("wheel", wheel);
    };
  }, [content]);
  useLayoutEffect(() => {
    const host = strip.current;
    const active = host?.querySelector<HTMLElement>(activeSelector);
    if (!host || !active) return;
    const reveal = () => {
      const frame = host.getBoundingClientRect();
      const tab = active.getBoundingClientRect();
      const style = getComputedStyle(host);
      const left = frame.left + (parseFloat(style.scrollPaddingLeft) || 0);
      const right = frame.right - (parseFloat(style.scrollPaddingRight) || 0);
      if (tab.left < left) host.scrollLeft += tab.left - left;
      else if (tab.right > right) host.scrollLeft += tab.right - right;
    };
    const observer = new ResizeObserver(reveal);
    observer.observe(host);
    observer.observe(active);
    reveal();
    return () => observer.disconnect();
  }, [activeKey, activeSelector]);
  return { strip, overflow };
}
