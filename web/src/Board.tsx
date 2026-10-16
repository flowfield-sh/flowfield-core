import { useEffect, useState, type ComponentProps } from "react";

/** Mouse panning starts only on empty board space; cards keep native interaction. */
export function Board({ className = "", ...props }: ComponentProps<"div">) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!element) return;
    let gesture: {
      id: number;
      x: number;
      left: number;
      moved: boolean;
    } | null = null;
    const measure = () => {
      element.setAttribute(
        "data-overflow",
        String(element.scrollWidth > element.clientWidth),
      );
    };
    const down = (event: PointerEvent) => {
      if (
        event.pointerType !== "mouse" ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey ||
        element.scrollWidth <= element.clientWidth ||
        !(event.target instanceof HTMLElement) ||
        !event.target.matches(".board, .column, .column-cards")
      )
        return;
      // Leave native scrollbar dragging alone.
      const target = event.target;
      const box = target.getBoundingClientRect();
      if (
        event.clientY >= box.top + target.clientTop + target.clientHeight ||
        event.clientX >= box.left + target.clientLeft + target.clientWidth
      )
        return;
      gesture = {
        id: event.pointerId,
        x: event.clientX,
        left: element.scrollLeft,
        moved: false,
      };
      element.setPointerCapture(event.pointerId);
    };
    const move = (event: PointerEvent) => {
      if (!gesture || gesture.id !== event.pointerId) return;
      const delta = event.clientX - gesture.x;
      if (!gesture.moved && Math.abs(delta) < 4) return;
      gesture.moved = true;
      element.setAttribute("data-panning", "true");
      event.preventDefault();
      element.scrollTo({ left: gesture.left - delta });
    };
    const stop = () => {
      if (gesture && element.hasPointerCapture(gesture.id))
        element.releasePointerCapture(gesture.id);
      gesture = null;
      element.removeAttribute("data-panning");
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    element.addEventListener("pointerdown", down);
    element.addEventListener("pointermove", move);
    element.addEventListener("pointerup", stop);
    element.addEventListener("pointercancel", stop);
    element.addEventListener("lostpointercapture", stop);
    window.addEventListener("blur", stop);
    return () => {
      stop();
      observer.disconnect();
      element.removeEventListener("pointerdown", down);
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerup", stop);
      element.removeEventListener("pointercancel", stop);
      element.removeEventListener("lostpointercapture", stop);
      window.removeEventListener("blur", stop);
    };
  }, [element]);
  return <div ref={setElement} className={`board ${className}`} {...props} />;
}
