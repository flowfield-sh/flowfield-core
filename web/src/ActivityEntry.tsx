import { createContext, type ReactNode } from "react";
import { Disclosure } from "./DetailLayout";
import { useState } from "react";
import { useRetainedState } from "./retainedState";
import { WorkState } from "./WorkState";
import type { components } from "./api-schema";

export const ActivityBodyVisible = createContext(true);

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
  alwaysOpen = false,
}: {
  identity: string;
  selected: boolean;
  title: ReactNode;
  state?: components["schemas"]["WorkState"] | null;
  metadata: ReactNode;
  children: ReactNode;
  inline?: boolean;
  hasBody: boolean;
  alwaysOpen?: boolean;
}) {
  const [manual, setManual] = useRetainedState<boolean | null>(
    `activity:${identity}:manual`,
    null,
  );
  const [previous, setPrevious] = useState({ selected, identity });
  if (previous.selected !== selected || previous.identity !== identity) {
    setPrevious({ selected, identity });
    if (selected) setManual(null);
  }
  const open = manual ?? selected;
  if (inline) return children;
  const heading = (
    <span className="activity-entry-heading">
      <span className="activity-entry-state">
        <WorkState state={state} indicatorOnly />
      </span>
      <span className="activity-entry-title">{title}</span>
      <span className="activity-entry-meta">{metadata}</span>
    </span>
  );
  if (alwaysOpen)
    return (
      <div className="activity-entry">
        {heading}
        {children}
      </div>
    );
  return hasBody ? (
    <Disclosure
      className="activity-entry"
      chevron={false}
      summary={heading}
      open={open}
      onClick={(event) => {
        if ((event.target as Element).closest("a,button")) return;
        if (
          (event.target as Element).closest("summary")?.parentElement ===
          event.currentTarget
        ) {
          event.preventDefault();
          setManual(!open);
        }
      }}
    >
      <ActivityBodyVisible.Provider value={open}>
        {children}
      </ActivityBodyVisible.Provider>
    </Disclosure>
  ) : (
    <div className="activity-entry activity-entry-record">{heading}</div>
  );
}
