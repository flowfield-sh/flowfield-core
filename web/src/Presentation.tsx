import { TaskLabels } from "./TaskLabels";
import { HeaderAction } from "./HeaderAction";
import { authorLabel } from "./workspace";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { ArrowLeft, X } from "lucide-react";
import { useContext, type ReactNode } from "react";
import { label, type TaskRevision, type TaskCard } from "./workspace";
import { usePaneClose, PaneHeading } from "./EntityPane";
import { Timestamp } from "./Timestamp";
import { ContentStack } from "./DetailLayout";
import { ResourceBackAvailable } from "./WorkspaceTabs";

export function CloseControl({
  label,
  close,
}: {
  label: string;
  close: () => void;
}) {
  const paneClose = usePaneClose();
  const backAvailable = useContext(ResourceBackAvailable);
  const dismiss = paneClose ?? close;
  return (
    <HeaderAction
      label={label}
      type="button"
      className="close-control"
      disabled={label.startsWith("Back") && !backAvailable}
      onClick={dismiss}
    >
      {label.startsWith("Back") ? <ArrowLeft /> : <X />}
    </HeaderAction>
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
    <PaneHeading>
      <div className="editor-heading">
        {closeLabel.startsWith("Back") && (
          <CloseControl label={closeLabel} close={close} />
        )}
        <ContentStack space="tight" className="min-w-0 flex-1 basis-44">
          <h2>{title}</h2>
          {metadata && <p className="detail-metadata">{metadata}</p>}
        </ContentStack>
        {actions && (
          <div className="editor-heading-actions actions">{actions}</div>
        )}
        {!closeLabel.startsWith("Back") && (
          <CloseControl label={closeLabel} close={close} />
        )}
      </div>
      {children}
    </PaneHeading>
  );
}

// One identity layout for board cards, archived rows, and task references.
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
    state?: TaskCard["state"];
  };
}) {
  return (
    <span className="task-identity">
      <span className="task-identity-heading">
        <span className="inline-flex items-center gap-2 align-baseline">
          <Badge variant="outline">{task.key}</Badge>
          <DraftBadge publicationStatus={task.publication_status} />
        </span>{" "}
        <strong>{title ?? task.title}</strong>
        {metadata}
      </span>
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
        <Badge variant="outline" className="bg-background" tabIndex={0}>
          Draft
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Ask the coordinator to prepare this task.</TooltipContent>
    </Tooltip>
  );
}
