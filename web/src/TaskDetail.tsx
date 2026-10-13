import { useEffect, useState } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { TaskDocuments } from "./TaskDocuments";
import { DetailHeader, DraftBadge } from "./Presentation";
import { MilestoneBadge } from "./MilestoneBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { projectHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, TaskState, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation, StageHeader } from "./TaskConversation";
import { AgentPermissions } from "./AgentPermissions";
import { label, type Board, type Task } from "./workspace";
export function TaskDetail({
  task,
  board,
  setUnsaved,
  close,
  openTask,
}: {
  task: Task;
  board: Board;
  setUnsaved: (dirty: boolean) => void;
  close: () => void;
  openTask: (id: string) => void;
}) {
  const [dirty, setDirty] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);
  useEffect(() => {
    setUnsaved(dirty || settingsDirty);
    return () => setUnsaved(false);
  }, [dirty, settingsDirty, setUnsaved]);
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={
          <>
            <WorkspaceLink to={projectHref(board.project.id)}>
              {board.project.name}
            </WorkspaceLink>
            {" / "}
            <span className="task-heading-key">{task.key}</span> {task.title}
          </>
        }
        close={close}
        closeLabel="Back to board"
      >
        <ContentStack className="task-conversation-header">
          <div className="actions task-references" aria-label="Task references">
            <TaskState
              sourceUpdates={false}
              projectId={board.project.id}
              open={openTask}
              task={{
                ...task,
                state: task.state ?? {
                  label: label(task.status),
                  tone: "idle",
                  href: null,
                },
              }}
            />
            <MilestoneBadge
              milestone={
                task.publication?.milestone ??
                board.milestones.find((m) => m.id === task.milestone_id)
              }
              preparedRevision={task.publication?.milestone?.revision}
              changed={!!task.milestone_update}
            />
            <DraftBadge publicationStatus={task.publication_status} />
            <TaskDocuments documents={task.publication?.documents ?? []} />
          </div>
          {!!task.labels.length && (
            <div className="entity-labels task-classifications">
              <TaskLabels ids={task.labels} />
            </div>
          )}
        </ContentStack>
      </DetailHeader>
      <ContentStack space="section" className="task-context">
        <AgentPermissions
          projectId={board.project.id}
          taskId={task.id}
          refresh={board}
        />
        <div
          className="task-definition content-stack"
          aria-label="Current task definition"
        >
          <ExpandableMarkdown key={task.id} title="Definition">
            {task.body}
          </ExpandableMarkdown>
        </div>
        <StageHeader projectId={board.project.id} task={task} refresh={board} />
        {hasTaskNeeds(task, board.pending_code[task.id], false) && (
          <DetailSection title="Waiting on">
            <TaskNeeds
              task={task}
              projectId={board.project.id}
              pendingCode={board.pending_code[task.id]}
              open={openTask}
              detailed
              showQuestions={false}
            />
          </DetailSection>
        )}
        {(task.prerequisites.some(
          (p) => !task.blocked_by.some((b) => b.key === p.key),
        ) ||
          task.dependents.length > 0) && (
          <Disclosure summary={<>Related work</>}>
            {!!task.dependents.length && (
              <p>
                <BlockedBy
                  prefix="Needed by:"
                  projectId={board.project.id}
                  tasks={task.dependents}
                  open={openTask}
                />
              </p>
            )}
            {!!task.prerequisites.length && (
              <p>
                <BlockedBy
                  prefix="Prerequisites:"
                  projectId={board.project.id}
                  tasks={task.prerequisites}
                  open={openTask}
                />
              </p>
            )}
          </Disclosure>
        )}
      </ContentStack>
      <TaskConversation
        projectId={board.project.id}
        task={task}
        board={board}
        refresh={board}
        onDirty={setDirty}
        onSettingsDirty={setSettingsDirty}
      />
    </section>
  );
}
