import type { ComponentProps, ReactNode } from "react";
import { ChevronRight, CircleHelp } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/** Layout owns spacing. Its children never contribute outside margins. */
export function ContentStack({
  children,
  space = "content",
  className = "",
  ...props
}: ComponentProps<"div"> & {
  space?: "flush" | "tight" | "content" | "section";
}) {
  return (
    <div {...props} className={`content-stack ${className}`} data-space={space}>
      {children}
    </div>
  );
}

export function DetailHeading({
  title,
  status,
  metadata,
  children,
  entry = false,
  titleAs = entry ? "div" : "h3",
  inlineMetadata = false,
}: {
  title: ReactNode;
  status?: ReactNode;
  metadata?: ReactNode;
  children?: ReactNode;
  entry?: boolean;
  titleAs?: "div" | "h2" | "h3";
  inlineMetadata?: boolean;
}) {
  const Title = titleAs;
  return (
    <header className="detail-heading-block">
      <div className="detail-heading-row">
        <Title
          className={entry ? "detail-title detail-entry-title" : "detail-title"}
        >
          {title}
        </Title>
        {status && <span className="detail-metadata">{status}</span>}
        {inlineMetadata && metadata && (
          <div className="detail-metadata">{metadata}</div>
        )}
      </div>
      {!inlineMetadata && metadata && (
        <p className="detail-metadata">{metadata}</p>
      )}
      {children}
    </header>
  );
}

export function DetailSection({
  title,
  children,
  className = "",
  actions,
  ...props
}: Omit<ComponentProps<"section">, "title"> & {
  title: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section {...props} className={`detail-section ${className}`}>
      {actions ? (
        <div className="detail-heading-row">
          <h4>{title}</h4>
          {actions}
        </div>
      ) : (
        <h4>{title}</h4>
      )}
      <ContentStack>{children}</ContentStack>
    </section>
  );
}

export function Disclosure({
  summary,
  children,
  className = "",
  help,
  group = false,
  ...props
}: ComponentProps<"details"> & {
  summary: ReactNode;
  help?: string;
  group?: boolean;
}) {
  return (
    <details
      {...props}
      className={`disclosure ${group ? "detail-group" : ""} ${className}`}
    >
      <summary className={group ? "detail-group-header" : undefined}>
        <ChevronRight className="disclosure-chevron" aria-hidden="true" />
        <span>{summary}</span>
        {help && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="disclosure-help"
                aria-label="About these checks"
                onClick={(event) => event.preventDefault()}
              >
                <CircleHelp aria-hidden="true" />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{help}</TooltipContent>
          </Tooltip>
        )}
      </summary>
      <ContentStack
        space={group ? "content" : "flush"}
        className={`disclosure-body ${group ? "detail-group-body" : ""}`}
      >
        {children}
      </ContentStack>
    </details>
  );
}

/** A bounded group of related evidence inside a detail/feed entry. */
export function DetailGroup({
  title,
  children,
  className = "",
  ...props
}: Omit<ComponentProps<"section">, "title"> & { title: string }) {
  return (
    <section
      {...props}
      className={`detail-group ${className}`}
      aria-label={title}
    >
      <header className="detail-group-header">
        <h4>{title}</h4>
      </header>
      <ContentStack className="detail-group-body">{children}</ContentStack>
    </section>
  );
}
