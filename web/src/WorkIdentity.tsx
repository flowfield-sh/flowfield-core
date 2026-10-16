import type { ComponentProps } from "react";
import { Badge } from "@/components/ui/badge";
import { WorkState } from "./WorkState";

/** The same identity order in headings, collections and related work. */
export function WorkIdentity({
  state,
  itemKey,
  title,
  decorative = false,
}: {
  state?: ComponentProps<typeof WorkState>["state"];
  itemKey?: string | null;
  title: string;
  decorative?: boolean;
}) {
  return (
    <span className="work-identity">
      <WorkState state={state} indicatorOnly decorative={decorative} />
      {itemKey && <Badge variant="outline">{itemKey}</Badge>}
      <span className="work-identity-title">{title}</span>
    </span>
  );
}
