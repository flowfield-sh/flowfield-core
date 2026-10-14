import { useState, type ReactNode } from "react";
import { ResponseFooterHost } from "./EntityOverlay";
import { ContentStack, DetailHeading } from "./DetailLayout";

/** One visual frame for human requests; response ownership stays with each resource. */
export function RequestFrame({
  kind,
  source,
  label,
  children,
}: {
  kind: string;
  source?: ReactNode;
  label: string;
  children: ReactNode;
}) {
  const [footer, setFooter] = useState<HTMLElement | null>(null);
  return (
    <article className="request-card" aria-label={label}>
      <DetailHeading entry title={kind} metadata={source} inlineMetadata />
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
