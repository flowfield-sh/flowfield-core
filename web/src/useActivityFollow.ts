import { useLayoutEffect, useRef } from "react";

/** Follow only the output viewport. Opening or new output never scrolls its ancestors. */
export function useActivityFollow(visible: boolean, ready: boolean) {
  const pane = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = pane.current,
      body = content.current;
    if (!visible || !ready || !node || !body) return;
    let following = true,
      shown = false,
      top = node.scrollTop;
    const settle = () => {
      if (!node.clientHeight) {
        shown = false;
        return;
      }
      if (!shown) {
        shown = true;
        following = true;
      }
      if (following) node.scrollTop = node.scrollHeight;
      top = node.scrollTop;
    };
    const scroll = () => {
      if (node.scrollTop === top) return;
      following = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
      top = node.scrollTop;
    };
    const inspect = (event: Event) => {
      if (event.target instanceof Element && event.target.closest("summary"))
        following = false;
    };
    const wheel = (event: WheelEvent) => {
      if (event.deltaY < 0) following = false;
    };
    const resize = new ResizeObserver(settle);
    resize.observe(body);
    resize.observe(node);
    node.addEventListener("scroll", scroll);
    node.addEventListener("click", inspect, true);
    node.addEventListener("wheel", wheel, { passive: true });
    settle();
    return () => {
      resize.disconnect();
      node.removeEventListener("scroll", scroll);
      node.removeEventListener("click", inspect, true);
      node.removeEventListener("wheel", wheel);
    };
  }, [visible, ready]);
  return { pane, content };
}
