import type { ReactNode } from "react";
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
  return (
    <article className="request-card" aria-label={label}>
      <DetailHeading entry title={kind} metadata={source} inlineMetadata />
      <ContentStack space="section" className="request-body">
        {children}
      </ContentStack>
    </article>
  );
}
