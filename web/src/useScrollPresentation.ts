import { useEffect } from "react";

/** Presentation only: the existing feed owner still handles following/history. */
export function useScrollPresentation(element: HTMLElement | null) {
  useEffect(() => {
    if (!element) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      element.setAttribute("data-overflow-top", String(element.scrollTop > 1));
    };
    const scroll = () => {
      measure();
      element.setAttribute("data-scrolling", "true");
      clearTimeout(timer);
      timer = setTimeout(() => element.removeAttribute("data-scrolling"), 700);
    };
    element.addEventListener("scroll", scroll, { passive: true });
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    measure();
    return () => {
      clearTimeout(timer);
      observer.disconnect();
      element.removeEventListener("scroll", scroll);
      element.removeAttribute("data-scrolling");
      element.removeAttribute("data-overflow-top");
    };
  }, [element]);
}
