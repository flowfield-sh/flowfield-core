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

const OverlayClose = createContext<(() => void) | null>(null);
export const useOverlayClose = () => useContext(OverlayClose);
const OverlayTitleHost = createContext<HTMLElement | null>(null);
const OverlayBody = createContext<HTMLElement | null>(null);
export const ResponseFooterHost = createContext<HTMLElement | null>(null);
export const useOverlayBody = () => useContext(OverlayBody);
export function OverlayFooter({
  children,
  inline = false,
}: {
  children: ReactNode;
  inline?: boolean;
}) {
  const host = useContext(ResponseFooterHost);
  return inline ? children : host ? createPortal(children, host) : null;
}
export function OverlayHeading({ children }: { children: ReactNode }) {
  const host = useContext(OverlayTitleHost);
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
      const scope = body?.closest(".workspace-pane");
      requestAnimationFrame(() => {
        // History can reopen a task while the prior tab/button has focus. Return
        // to its visible collection entry, and never steal from a newer pane.
        if (document.querySelector(".entity-pane")) return;
        const entry = [
          ...(scope?.querySelectorAll<HTMLAnchorElement>("a[href]") ?? []),
        ].find(
          (link) =>
            (link.pathname === returnFocusHref ||
              link.pathname.startsWith(returnFocusHref + "/")) &&
            link.getClientRects().length > 0,
        );
        const target = entry ?? opener.current;
        if (target?.isConnected) target.focus({ preventScroll: true });
      });
    },
    [body, returnFocusHref],
  );
  return (
    <OverlayClose.Provider value={close}>
      <OverlayTitleHost.Provider value={title}>
        <OverlayBody.Provider value={body}>
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
              <div className="entity-overlay-title" ref={setTitle} />
              <div className="entity-overlay-body" ref={setBody} tabIndex={-1}>
                {children}
              </div>
              <div className="entity-overlay-footer" ref={setFooter} />
            </section>
          </ResponseFooterHost.Provider>
        </OverlayBody.Provider>
      </OverlayTitleHost.Provider>
    </OverlayClose.Provider>
  );
}
