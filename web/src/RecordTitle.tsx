import { Badge } from "@/components/ui/badge";

/** Format app-authored record references; user prose and ordinary counts stay untouched. */
export function RecordTitle({ children }: { children: string }) {
  return children
    .split(/((?:result|correction|follow-up|revision|attempt|review) #\d+)/gi)
    .map((part, index) => {
      const match = /^(.*) #(\d+)$/.exec(part);
      return match ? (
        <span key={index} className="whitespace-nowrap">
          {match[1]}{" "}
          <Badge
            variant="outline"
            className="h-4 overflow-visible px-1 py-0 align-baseline text-[11px] leading-none font-normal"
          >
            <span className="sr-only">#</span>
            {match[2]}
          </Badge>
        </span>
      ) : (
        part
      );
    });
}
