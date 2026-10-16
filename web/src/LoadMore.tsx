import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";

/** Pagination stays inside its collection, with room around the action. */
export function LoadMore(props: ComponentProps<typeof Button>) {
  return (
    <div className="pagination-controls actions">
      <Button type="button" size="sm" variant="outline" {...props} />
    </div>
  );
}
