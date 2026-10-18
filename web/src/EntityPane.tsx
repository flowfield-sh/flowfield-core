import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

const PaneClose = createContext<(() => void) | null>(null);
export const usePaneClose = () => useContext(PaneClose);
const PaneTitleHost = createContext<HTMLElement | null>(null);
const PaneBody = createContext<HTMLElement | null>(null);
export const ResponseFooterHost = createContext<HTMLElement | null>(null);
export const usePaneBody = () => useContext(PaneBody);
export function PaneFooter({
  children,
  inline = false,
}: {
  children: ReactNode;
  inline?: boolean;
}) {
  const host = useContext(ResponseFooterHost);
  return inline ? children : host ? createPortal(children, host) : null;
}
export function PaneHeading({ children }: { children: ReactNode }) {
  const host = useContext(PaneTitleHost);
  return host ? createPortal(children, host) : children;
}

// Routed details share one heading, scroll and response-footer owner.
export function EntityPane({
  label = "Task details",
  children,
  close,
  identity,
  returnFocusHref,
}: {
  children: ReactNode;
  close: () => void;
  identity: string;
  returnFocusHref: string;
  label?: string;
}) {
  const [title, setTitle] = useState<HTMLDivElement | null>(null);
  const [body, setBody] = useState<HTMLDivElement | null>(null);
  const [footer, setFooter] = useState<HTMLDivElement | null>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      // Saving can remove a focused control. Preserve Escape when focus falls
      // back to the document, without intercepting the Coordinator's keys.
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        (event.target === document.body ||
          (event.target instanceof HTMLElement &&
            event.target.matches(".workspace-resource-content") &&
            !!body &&
            event.target.contains(body)))
      ) {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [close, body]);
  useLayoutEffect(() => {
    body?.scrollTo({ top: 0 });
    body?.focus({ preventScroll: true });
  }, [identity, body]);
  useLayoutEffect(
    () => () => {
      // Installing the body ref is not navigation away from the pane.
      if (!body) return;
      const scope = body?.closest(".workspace-pane");
      requestAnimationFrame(() => {
        // History can reopen a task while the prior tab/button has focus. Return
        // to its visible collection entry, and never steal from a newer pane.
        const entry = [
          ...((scope ?? document).querySelectorAll<HTMLAnchorElement>(
            "a[href]",
          ) ?? []),
        ].find(
          (link) =>
            (link.pathname === returnFocusHref ||
              link.pathname.startsWith(returnFocusHref + "/")) &&
            link.getClientRects().length > 0,
        );
        const currentPane = [...document.querySelectorAll(".entity-pane")]
          .filter((pane) => pane.getClientRects().length > 0)
          .at(-1);
        // A routed pane may mount after the clicked link lost focus. An old
        // body/outside-pane opener must not suppress the matching return link.
        const target = [opener.current, entry].find(
          (candidate) =>
            candidate &&
            candidate !== document.body &&
            candidate.isConnected &&
            candidate.getClientRects().length > 0 &&
            (!currentPane || currentPane.contains(candidate)),
        );
        target?.focus({ preventScroll: true });
      });
    },
    [body, returnFocusHref],
  );
  return (
    <PaneClose.Provider value={close}>
      <PaneTitleHost.Provider value={title}>
        <PaneBody.Provider value={body}>
          <ResponseFooterHost.Provider value={footer}>
            <section
              className="entity-pane"
              aria-label={label}
              onKeyDown={(event) => {
                if (event.key === "Escape" && !event.defaultPrevented) {
                  event.preventDefault();
                  close();
                }
              }}
            >
              <div className="entity-pane-title" ref={setTitle} />
              <div className="entity-pane-body" ref={setBody} tabIndex={-1}>
                {children}
              </div>
              <div className="entity-pane-footer" ref={setFooter} />
            </section>
          </ResponseFooterHost.Provider>
        </PaneBody.Provider>
      </PaneTitleHost.Provider>
    </PaneClose.Provider>
  );
}
