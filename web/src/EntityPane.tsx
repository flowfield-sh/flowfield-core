import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

const PaneClose = createContext<(() => void) | null>(null);
export const usePaneClose = () => useContext(PaneClose);
const PaneTitleHost = createContext<HTMLElement | null>(null);
const ResponseFooterHost = createContext<HTMLElement | null>(null);
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
  responseFooter = true,
}: {
  children: ReactNode;
  close: () => void;
  identity: string;
  returnFocusHref: string;
  responseFooter?: boolean;
  label?: string;
}) {
  const [title, setTitle] = useState<HTMLDivElement | null>(null);
  const [body, setBody] = useState<HTMLDivElement | null>(null);
  const [footer, setFooter] = useState<HTMLDivElement | null>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
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
        const target = [entry, opener.current].find(
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
        <ResponseFooterHost.Provider value={footer}>
          <section className="entity-pane" aria-label={label}>
            <div className="entity-pane-title" ref={setTitle} />
            <div className="entity-pane-body" ref={setBody} tabIndex={-1}>
              {children}
            </div>
            {responseFooter && (
              <div className="entity-pane-footer" ref={setFooter} />
            )}
          </section>
        </ResponseFooterHost.Provider>
      </PaneTitleHost.Provider>
    </PaneClose.Provider>
  );
}
