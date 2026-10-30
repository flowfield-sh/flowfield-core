import type { ComponentProps, ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { WorkState } from "./WorkState";

/** The same identity order in headings, collections and related work. */
export function WorkIdentity({
  state,
  itemKey,
  title,
  decorative = false,
  detail,
}: {
  state?: ComponentProps<typeof WorkState>["state"];
  itemKey?: string | null;
  title: string;
  decorative?: boolean;
  detail?: ReactNode;
}) {
  return (
    <span className="work-identity">
      <WorkState state={state} indicatorOnly decorative={decorative} />
      {itemKey && (
        <span className="work-key inline-flex shrink-0 items-center gap-1">
          <Badge variant="outline">{itemKey}</Badge>
        </span>
      )}
      {detail ? (
        <span className="min-w-0">
          <span className="work-identity-title">{title}</span>
          <span className="block text-xs text-muted-foreground">{detail}</span>
        </span>
      ) : (
        <span className="work-identity-title">{title}</span>
      )}
    </span>
  );
}
