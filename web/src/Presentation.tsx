import { TaskLabels } from "./TaskLabels";
import { authorLabel } from "./workspace";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { ArrowLeft, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type ReactNode } from "react";
import { followLink } from "./navigation";
import { label, type TaskRevision } from "./workspace";
import { useOverlayClose, OverlayHeading } from "./EntityOverlay";
import { ExactTimeTooltip, Timestamp } from "./Timestamp";

export function CloseControl({
  label,
  close,
}: {
  label: string;
  close: () => void;
}) {
  const overlayClose = useOverlayClose();
  const dismiss = overlayClose ?? close;
  return (
    <Button
      size="icon-sm"
      variant="ghost"
      type="button"
      className={`close-control ${label === "Back to board" ? "-ms-2" : "-me-2"}`}
      aria-label={label}
      title={label}
      onClick={dismiss}
    >
      {label === "Back to board" ? <ArrowLeft /> : <X />}
    </Button>
  );
}

export function CollectionLayout({ children }: { children: ReactNode }) {
  return <div className="collection-layout">{children}</div>;
}

export function CollectionRow({
  children,
  href,
  selected,
  open,
  className = "",
  timestamp,
}: {
  children: ReactNode;
  href: string;
  selected?: boolean;
  open: () => void;
  className?: string;
  timestamp?: string;
}) {
  const link = (
    <a
      className={`collection-row ${className}${selected ? " selected" : ""}`}
      href={href}
      onClick={(event) => followLink(event, open)}
    >
      {children}
    </a>
  );
  return timestamp ? (
    <ExactTimeTooltip date={timestamp}>{link}</ExactTimeTooltip>
  ) : (
    link
  );
}

export function Attribution({
  author,
  date,
}: {
  author: string;
  date: string;
}) {
  return (
    <span className="record-attribution">
      {authorLabel(author)} · <Timestamp date={date} tooltip={false} />
    </span>
  );
}

export function DetailHeader({
  title,
  close,
  closeLabel = "Close editor",
  actions,
  children,
}: {
  title: ReactNode;
  close: () => void;
  closeLabel?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <OverlayHeading>
      <div className="editor-heading">
        {closeLabel === "Back to board" && (
          <CloseControl label={closeLabel} close={close} />
        )}
        <h2 className="min-w-0 flex-1">{title}</h2>
        {actions}
        {closeLabel !== "Back to board" && (
          <CloseControl label={closeLabel} close={close} />
        )}
      </div>
      {children}
    </OverlayHeading>
  );
}

// One identity layout for board cards, archived rows, and milestone members.
export function TaskIdentity({
  task,
  labels,
  title,
}: {
  labels?: ReactNode;
  title?: ReactNode;
  task: Pick<TaskRevision, "key" | "title" | "labels"> & {
    status?: string;
    publication_status?: string;
  };
}) {
  return (
    <span className="task-identity">
      <span className="task-labels">
        <span className="task-key">{task.key}</span>
        {labels ?? (
          <span className="task-kind-labels">
            <TaskLabels ids={task.labels} />
          </span>
        )}
      </span>
      <strong>{title ?? task.title}</strong>
    </span>
  );
}

export function DetailTabs<T extends string>({
  id,
  title,
  tabs,
  active,
  change,
}: {
  id: string;
  title: string;
  tabs: readonly T[];
  active: T;
  change: (tab: T) => void;
}) {
  return (
    <Tabs
      activationMode="manual"
      value={active}
      onValueChange={(value) => change(value as T)}
    >
      <TabsList
        variant="line"
        className="card-tabs w-full justify-start"
        aria-label={title}
      >
        {tabs.map((tab) => (
          <TabsTrigger
            className="flex-none"
            key={tab}
            value={tab}
            id={`${id}-${tab}-tab`}
            aria-controls={`${id}-${tab}`}
          >
            {label(tab)}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}

export function DraftBadge({
  publicationStatus,
}: {
  publicationStatus?: string;
}) {
  if (publicationStatus !== "draft") return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" tabIndex={0}>
          Draft
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Ask the coordinator to prepare this task.</TooltipContent>
    </Tooltip>
  );
}
