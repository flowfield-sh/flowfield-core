import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Toaster as Sonner, type ToasterProps } from "sonner";

const Viewport = createContext<{
  host: HTMLElement;
  attach: (target: HTMLElement) => () => void;
} | null>(null);

// Keep one Sonner instance and its timers alive while moving its DOM into the
// current modal's focus scope. Radix still owns focus and outside interaction.
export function ToastProvider({ children }: { children: ReactNode }) {
  const [host] = useState(() => document.createElement("div"));
  const targets = useRef<HTMLElement[]>([]);
  const attach = useCallback(
    (target: HTMLElement) => {
      targets.current.push(target);
      target.appendChild(host);
      return () => {
        targets.current = targets.current.filter((item) => item !== target);
        const previous = targets.current.at(-1);
        if (previous) previous.appendChild(host);
        else host.remove();
      };
    },
    [host],
  );
  return (
    <Viewport.Provider value={{ host, attach }}>
      <ToastHost />
      {children}
    </Viewport.Provider>
  );
}

export function ToastHost() {
  const attach = useContext(Viewport)?.attach;
  const target = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (target.current) return attach?.(target.current);
  }, [attach]);
  return <div ref={target} style={{ position: "absolute" }} />;
}

export function Toaster(props: ToasterProps) {
  const viewport = useContext(Viewport);
  if (!viewport) return null;
  return createPortal(
    <Sonner
      position="bottom-right"
      richColors
      closeButton
      duration={5000}
      visibleToasts={3}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as CSSProperties
      }
      {...props}
    />,
    viewport.host,
  );
}
