import type { ReactElement } from "react";
import { useTimeAgo } from "react-time-ago";
import "react-time-ago/locale/en";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const exactTime = new Intl.DateTimeFormat(undefined, {
  dateStyle: "full",
  timeStyle: "long",
});
const readableTime = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Also wraps timestamp-bearing card links, keeping their single keyboard stop. */
export function ExactTimeTooltip({
  date,
  children,
}: {
  date: string;
  children: ReactElement;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent collisionPadding={8}>
        {exactTime.format(new Date(date))}
      </TooltipContent>
    </Tooltip>
  );
}

export function Timestamp({
  date,
  tooltip = true,
  format = "relative",
}: {
  date: string;
  tooltip?: boolean;
  format?: "relative" | "absolute";
}) {
  const { formattedDate } = useTimeAgo({
    date: Date.parse(date),
    locale: "en",
    polyfill: false,
  });
  const time = (
    <time
      className="timestamp"
      dateTime={date}
      tabIndex={tooltip ? 0 : undefined}
      aria-label={exactTime.format(new Date(date))}
    >
      {format === "absolute"
        ? readableTime.format(new Date(date))
        : formattedDate}
    </time>
  );
  return tooltip ? (
    <ExactTimeTooltip date={date}>{time}</ExactTimeTooltip>
  ) : (
    time
  );
}
