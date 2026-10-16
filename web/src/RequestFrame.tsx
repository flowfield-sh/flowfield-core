import { OpenInWorkTab } from "./WorkspaceTabs";
import { useState, type ReactNode } from "react";
import { ResponseFooterHost } from "./EntityOverlay";
import { ContentStack, DetailHeading } from "./DetailLayout";
import { WorkState } from "./WorkState";
import type { components } from "./api-schema";

/** One visual frame for human requests; response ownership stays with each resource. */
export function RequestFrame({
  kind,
  source,
  label,
  state,
  children,
  href,
}: {
  kind: string;
  href?: string;
  source?: ReactNode;
  label: string;
  state: components["schemas"]["WorkState"] | null | undefined;
  children: ReactNode;
}) {
  const [footer, setFooter] = useState<HTMLElement | null>(null);
  return (
    <article className="request-card" aria-label={label}>
      <DetailHeading
        entry
        title={
          <span className="feed-event-title">
            <WorkState
              state={state ?? { label: kind, tone: "waiting" }}
              indicatorOnly
            />
            {source}
            <span>{kind}</span>
          </span>
        }
        metadata={
          href && <OpenInWorkTab href={href} label="Open request in work tab" />
        }
        inlineMetadata
      />
      <ResponseFooterHost.Provider value={footer}>
        <ContentStack space="section" className="request-body">
          {children}
        </ContentStack>
        <footer
          className="request-footer"
          aria-label="Response"
          ref={setFooter}
        />
      </ResponseFooterHost.Provider>
    </article>
  );
}
