import { createContext, useLayoutEffect, useRef, type ReactNode } from "react";
import { useLocation } from "react-router";

const positions = new Map<string, Map<string, { top: number; left: number }>>();
const focusLinks = new Map<string, string>();
export const ResourceScrollKey = createContext<string | null>(null);
const feedOwns = (node: HTMLElement) =>
  node.matches(".entity-overlay-body") &&
  !!node.querySelector(".conversation-messages");
const selector =
  ".entity-overlay-body, .workspace-work-content, .column-cards, .board, .milestone-board";
export function ResourceScroll({
  tab,
  children,
}: {
  tab: string;
  children: ReactNode;
}) {
  const location = useLocation();
  const root = useRef<HTMLDivElement>(null);
  const key = tab + ":" + location.pathname + location.search;
  useLayoutEffect(() => {
    const host = root.current;
    if (!host) return;
    const saved = positions.get(key);
    let restoring = !!saved;
    let restoreFocus = focusLinks.get(key);
    const identity = (node: HTMLElement) =>
      node.className + ":" + (node.getAttribute("aria-label") ?? "");
    function restore() {
      if (restoreFocus && host?.isConnected) {
        const link = [
          ...host.querySelectorAll<HTMLAnchorElement>("a[href]"),
        ].find(
          (node) =>
            node.getAttribute("href") === restoreFocus &&
            node.getClientRects().length,
        );
        if (link) {
          link.focus({ preventScroll: true });
          restoreFocus = undefined;
        }
      }
      if (!restoring || !saved) return;
      for (const node of host!.querySelectorAll<HTMLElement>(selector)) {
        if (feedOwns(node)) continue;
        const value = saved.get(identity(node));
        if (value) {
          node.scrollTop = value.top;
          node.scrollLeft = value.left;
        }
      }
    }
    function remember(event: Event) {
      if (restoring) return;
      const node = event.target;
      if (!(node instanceof HTMLElement) || !node.matches(selector)) return;
      if (feedOwns(node)) return;
      const values = positions.get(key) ?? new Map();
      values.set(identity(node), {
        top: node.scrollTop,
        left: node.scrollLeft,
      });
      positions.delete(key);
      positions.set(key, values);
      while (positions.size > 160)
        positions.delete(positions.keys().next().value!);
    }
    function interact() {
      restoring = false;
      restoreFocus = undefined;
    }
    function cancelFocusRestoration() {
      restoreFocus = undefined;
    }
    function rememberFocus(event: FocusEvent) {
      const node = event.target;
      if (!(node instanceof HTMLAnchorElement)) return;
      focusLinks.delete(key);
      focusLinks.set(key, node.getAttribute("href")!);
      while (focusLinks.size > 160)
        focusLinks.delete(focusLinks.keys().next().value!);
    }
    const observer = new MutationObserver(restore);
    observer.observe(host, { childList: true, subtree: true });
    const resize = new ResizeObserver(restore);
    resize.observe(host);
    host.addEventListener("scroll", remember, true);
    host.addEventListener("focusin", rememberFocus);
    host.addEventListener("wheel", interact, { passive: true });
    host.addEventListener("pointerdown", interact);
    host.addEventListener("keydown", interact);
    document.addEventListener("pointerdown", cancelFocusRestoration, true);
    document.addEventListener("keydown", cancelFocusRestoration, true);
    restore();
    return () => {
      observer.disconnect();
      resize.disconnect();
      host.removeEventListener("scroll", remember, true);
      host.removeEventListener("focusin", rememberFocus);
      host.removeEventListener("wheel", interact);
      host.removeEventListener("pointerdown", interact);
      host.removeEventListener("keydown", interact);
      document.removeEventListener("pointerdown", cancelFocusRestoration, true);
      document.removeEventListener("keydown", cancelFocusRestoration, true);
    };
  }, [key]);
  return (
    <div className="workspace-resource-scroll" ref={root}>
      <ResourceScrollKey.Provider value={key}>
        {children}
      </ResourceScrollKey.Provider>
    </div>
  );
}
