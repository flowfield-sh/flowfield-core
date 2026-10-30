import { useTabOverflow } from "./useTabOverflow";
import { TaskLabels } from "./TaskLabels";
import { HeaderAction } from "./HeaderAction";
import { authorLabel } from "./workspace";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";

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
  if (label.startsWith("Back") && !backAvailable) return null;
  return (
    <HeaderAction
      label={label}
      type="button"
      className="close-control"
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
        <ContentStack space="tight" className="min-w-0 flex-1 basis-auto">
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
    state?: TaskCard["state"];
  };
}) {
  return (
    <span className="task-identity">
      <span className="task-identity-heading">
        <span className="inline-flex items-center gap-1 align-baseline">
          <Badge variant="outline">{task.key}</Badge>
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
  indicators,
}: {
  id: string;
  title: string;
  tabs: readonly T[];
  active: T;
  change: (tab: T) => void;
  indicators?: Partial<Record<T, string>>;
}) {
  const { strip, overflow } = useTabOverflow(
    tabs,
    active,
    '[role="tab"][data-state="active"]',
  );
  return (
    <Tabs
      className="min-w-0"
      activationMode="manual"
      value={active}
      onValueChange={(value) => change(value as T)}
    >
      <TabsList
        variant="line"
        className="card-tabs tab-overflow h-9 w-full justify-start overflow-x-auto p-0"
        ref={strip}
        data-overflow-left={overflow.left}
        data-overflow-right={overflow.right}
        aria-label={title}
      >
        {tabs.map((tab) => (
          <TabsTrigger
            className="h-full flex-none rounded-none group-data-[orientation=horizontal]/tabs:after:bottom-[-1px]"
            key={tab}
            value={tab}
            id={`${id}-${tab}-tab`}
            aria-controls={`${id}-${tab}`}
            aria-label={indicators?.[tab] ? label(tab) : undefined}
            aria-describedby={
              indicators?.[tab] ? `${id}-${tab}-indicator` : undefined
            }
          >
            {label(tab)}
            {indicators?.[tab] && (
              <span
                id={`${id}-${tab}-indicator`}
                className="h-2 w-2 shrink-0 rounded-full bg-primary"
                role="status"
                aria-label={indicators[tab]}
              />
            )}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
