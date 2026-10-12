import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";

/** Shared board/archive frame. The primary link stretches; controls sit above it. */
export function WorkCard({
  children,
  selected = false,
}: {
  children: ReactNode;
  selected?: boolean;
}) {
  return (
    <Card className={`task-card gap-1 p-3${selected ? " picked" : ""}`}>
      {children}
    </Card>
  );
}
