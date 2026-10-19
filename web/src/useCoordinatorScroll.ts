import { useLayoutEffect, useRef, type RefObject } from "react";

const initialPosition = () => ({
  ready: false,
  following: true,
  initialized: false,
  interacted: false,
  anchor: null as { id: string; offset: number } | null,
  top: 0,
  height: 0,
  viewport: 0,
});

/** Follow the live end, or preserve the first visible entry while reading history.
 * Observe layout as well as records: output, checks and Markdown load independently.
 */
export function useCoordinatorScroll(
  root: RefObject<HTMLDivElement | null>,
  ready: boolean,
  pane: HTMLDivElement | null,
) {
  const control = useRef(initialPosition());
  const update = useRef<() => void>(() => {});
  useLayoutEffect(() => {
    const content = root.current;
    if (!content || !pane || !ready) return;
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
      // Ignore queued scroll events from our own positioning.
      if (pane.scrollTop === state.top) return;
      state.following =
        pane.scrollHeight - pane.scrollTop - pane.clientHeight < 24;
      remember();
    };
    update.current = settle;
    const observer = new ResizeObserver(settle);
    observer.observe(content);
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
  }, [root, pane, ready]);
  useLayoutEffect(() => {
    const state = control.current;
    state.ready = ready;
    if (!pane || !ready || state.initialized) return;
    state.following = !state.interacted;
    const frame = requestAnimationFrame(() => {
      state.top = pane.scrollTop;
      state.height = pane.scrollHeight;
      state.viewport = pane.clientHeight;
      state.initialized = true;
      update.current();
    });
    return () => cancelAnimationFrame(frame);
  }, [pane, ready]);
  return {
    readingEarlier: () => {
      control.current.following = false;
    },
  };
}
