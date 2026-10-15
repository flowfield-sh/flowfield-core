import { useLayoutEffect, useRef, type RefObject } from "react";
import { useOverlayBody } from "./EntityOverlay";

/** Follow the live end, or preserve the first visible entry while reading history.
 * Observe layout as well as records: output, checks and Markdown load independently.
 */
export function useFeedScroll(
  root: RefObject<HTMLDivElement | null>,
  ready: boolean,
  target: string | undefined,
  targetReady: boolean,
  scrollPane?: HTMLDivElement | null,
) {
  const overlayPane = useOverlayBody();
  const pane = scrollPane ?? overlayPane;
  const control = useRef({
    ready: false,
    following: true,
    initialized: false,
    interacted: false,
    target: "",
    anchor: null as { id: string; offset: number } | null,
    top: 0,
    height: 0,
    viewport: 0,
  });
  const update = useRef<() => void>(() => {});
  useLayoutEffect(() => {
    const content = root.current;
    if (!content || !pane) return;
    const state = control.current;
    function remember(captureAnchor = true) {
      state.top = pane!.scrollTop;
      state.height = pane!.scrollHeight;
      state.viewport = pane!.clientHeight;
      if (!captureAnchor) return;
      const top = pane!.getBoundingClientRect().top;
      const inset = parseFloat(getComputedStyle(pane!).scrollPaddingTop) || 0;
      const entry = [
        ...content!.querySelectorAll<HTMLElement>("[data-message-id]"),
      ].find((node) => node.getBoundingClientRect().bottom > top + inset);
      state.anchor = entry
        ? {
            id: entry.dataset.messageId!,
            offset: entry.getBoundingClientRect().top - top,
          }
        : null;
    }
    function settle() {
      if (!state.ready || !state.initialized) return;
      // A user scroll can precede the browser's queued scroll event. Do not
      // restore yesterday's anchor when a live render lands in that interval.
      if (
        pane!.scrollTop !== state.top &&
        pane!.scrollHeight === state.height &&
        pane!.clientHeight === state.viewport
      )
        scroll();
      if (state.following) pane!.scrollTo({ top: pane!.scrollHeight });
      else if (state.anchor) {
        const entry = content!.querySelector<HTMLElement>(
          `[data-message-id="${CSS.escape(state.anchor.id)}"]`,
        );
        if (entry)
          pane!.scrollTo({
            top:
              pane!.scrollTop +
              entry.getBoundingClientRect().top -
              pane!.getBoundingClientRect().top -
              state.anchor.offset,
          });
        else pane!.scrollTo({ top: state.top });
      }
      // Layout correction keeps the chosen entry, even if an earlier entry
      // expands. Only actual reading movement chooses a new history anchor.
      remember(state.following);
    }
    const scroll = () => {
      if (!state.ready || !state.initialized) return;
      // Asynchronous content/footer layout can clamp scrollTop before ResizeObserver
      // runs. Preserve both following and historical reading through that adjustment.
      if (
        pane.scrollHeight !== state.height ||
        pane.clientHeight !== state.viewport
      ) {
        settle();
        return;
      }
      // Our positioning also queues a scroll event. It must not turn an exact
      // permalink into follow mode just because its loading placeholder fits.
      if (pane.scrollTop === state.top) return;
      state.following =
        pane.scrollHeight - pane.scrollTop - pane.clientHeight < 24;
      remember();
    };
    update.current = settle;
    const observer = new ResizeObserver(settle);
    observer.observe(content.closest(".task-detail") ?? content);
    observer.observe(pane);
    const interact = () => {
      if (!state.initialized) {
        state.interacted = true;
        state.following = false;
        remember();
      }
    };
    const inspect = (event: Event) => {
      if (event.target instanceof Element && event.target.closest("summary")) {
        // Disclosure clicks (including keyboard activation) are explicit reading
        // intent. Capture the anchor before native details changes the layout.
        state.following = false;
        state.interacted = true;
        remember();
      }
    };
    const intentEvents = [
      "wheel",
      "touchstart",
      "pointerdown",
      "keydown",
    ] as const;
    for (const event of intentEvents)
      pane.addEventListener(event, interact, { passive: true });
    pane.addEventListener("scroll", scroll);
    pane.addEventListener("click", inspect, true);
    settle();
    return () => {
      observer.disconnect();
      pane.removeEventListener("scroll", scroll);
      pane.removeEventListener("click", inspect, true);
      for (const event of intentEvents)
        pane.removeEventListener(event, interact);
    };
  }, [root, pane]);
  useLayoutEffect(() => {
    const state = control.current;
    state.ready = ready && (!target || targetReady);
    if (!pane || !state.ready) return;
    const nextTarget = target ?? "";
    if (state.initialized && state.target === nextTarget) return;
    if (state.initialized) state.interacted = false;
    state.initialized = false;
    state.target = nextTarget;
    state.following = !target && !state.interacted;
    // The overlay resets its body in a parent layout effect. Ignore its queued
    // scroll events until our own initial positioning has run after that reset.
    const frame = requestAnimationFrame(() => {
      const entry =
        target && !state.interacted
          ? root.current?.querySelector<HTMLElement>(
              `[data-message-id="${CSS.escape(target)}"]`,
            )
          : null;
      if (entry) {
        state.anchor = {
          id: target!,
          // Keep the intended inset, not a position clamped by a short loading
          // placeholder. Later content/viewport growth can then honor the link.
          offset:
            (parseFloat(getComputedStyle(pane).scrollPaddingTop) || 0) +
            (parseFloat(getComputedStyle(entry).scrollMarginTop) || 0),
        };
      } else state.anchor = null;
      state.top = pane.scrollTop;
      state.height = pane.scrollHeight;
      state.viewport = pane.clientHeight;
      state.initialized = true;
      update.current();
    });
    return () => cancelAnimationFrame(frame);
  }, [pane, ready, root, target, targetReady]);
  return {
    readingEarlier: () => {
      control.current.following = false;
    },
  };
}
