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
import { useContext, type ReactNode } from "react";
import { label, type TaskRevision } from "./workspace";
import { useOverlayClose, OverlayHeading } from "./EntityOverlay";
import { Timestamp } from "./Timestamp";
import { ContentStack } from "./DetailLayout";
import { OpenInWorkTab, ResourceBackAvailable } from "./WorkspaceTabs";

export function CloseControl({
  label,
  close,
}: {
  label: string;
  close: () => void;
}) {
  const overlayClose = useOverlayClose();
  const backAvailable = useContext(ResourceBackAvailable);
  const dismiss = overlayClose ?? close;
  return (
    <Button
      size="icon-sm"
      variant="ghost"
      type="button"
      className={`close-control ${label.startsWith("Back") ? "-ms-2" : "-me-2"}`}
      aria-label={label}
      title={label}
      disabled={label.startsWith("Back") && !backAvailable}
      onClick={dismiss}
    >
      {label.startsWith("Back") ? <ArrowLeft /> : <X />}
    </Button>
  );
}

export function ArchiveAttribution({
  author,
  date,
}: {
  author: string;
  date: string;
}) {
  return (
    <span className="record-attribution">
      Archived by {authorLabel(author)} · <Timestamp date={date} />
    </span>
  );
}

export function DetailHeader({
  title,
  close,
  closeLabel = "Back",
  actions,
  metadata,
  children,
}: {
  title: ReactNode;
  close: () => void;
  closeLabel?: string;
  actions?: ReactNode;
  metadata?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <OverlayHeading>
      <div className="editor-heading">
        {closeLabel.startsWith("Back") && (
          <div className="detail-navigation">
            <CloseControl label={closeLabel} close={close} />
            <OpenInWorkTab />
          </div>
        )}
        <ContentStack space="tight" className="min-w-0 flex-1 basis-44">
          <h2>{title}</h2>
          {metadata && <p className="detail-metadata">{metadata}</p>}
        </ContentStack>
        {actions && <div className="editor-heading-actions">{actions}</div>}
        {!closeLabel.startsWith("Back") && (
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
  metadata,
  title,
}: {
  metadata?: ReactNode;
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
        {metadata}
      </span>
      <strong>{title ?? task.title}</strong>
      {task.labels.length > 0 && (
        <span className="task-labels">
          <TaskLabels ids={task.labels} />
        </span>
      )}
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
        variant="pill"
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
        <Badge variant="outline" className="bg-background" tabIndex={0}>
          Draft
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Ask the coordinator to prepare this task.</TooltipContent>
    </Tooltip>
  );
}
