import { useEffect, type ReactNode } from "react";
import { Disclosure } from "./DetailLayout";
import { useRetainedState } from "./retainedState";
import { WorkState } from "./WorkState";
import type { components } from "./api-schema";

/** Compact presentation of canonical events; response controls retain their owner. */
export function ActivityEntry({
  identity,
  selected,
  title,
  state,
  metadata,
  children,
  inline = false,
  hasBody,
}: {
  identity: string;
  selected: boolean;
  title: ReactNode;
  state?: components["schemas"]["WorkState"] | null;
  metadata: ReactNode;
  children: ReactNode;
  inline?: boolean;
  hasBody: boolean;
}) {
  const [open, setOpen] = useRetainedState(`activity:${identity}`, selected);
  useEffect(() => {
    if (selected) setOpen(true);
  }, [selected, setOpen]);
  if (inline) return children;
  const heading = (
    <span className="activity-entry-heading">
      <WorkState state={state} indicatorOnly />
      <span className="activity-entry-title">{title}</span>
      <span className="activity-entry-meta">{metadata}</span>
    </span>
  );
  return hasBody ? (
    <Disclosure
      className="activity-entry"
      summary={heading}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      {children}
    </Disclosure>
  ) : (
    <div className="activity-entry activity-entry-record">{heading}</div>
  );
}
