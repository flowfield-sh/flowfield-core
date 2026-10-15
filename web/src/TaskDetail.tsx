import { useParams } from "react-router";
import { Button } from "@/components/ui/button";
import { WorkState } from "./WorkState";
import { SourceReferences } from "./SourceReferences";
import type { DirtyCallback } from "./retainedState";
import { useCallback, useEffect, useState } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader, DraftBadge } from "./Presentation";
import { MilestoneBadge } from "./MilestoneBadge";
import { WorkspaceLink } from "./WorkspaceLink";
import { projectHref, taskHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation, StageHeader } from "./TaskConversation";
import { AgentPermissions } from "./AgentPermissions";
import { type Board, type Task } from "./workspace";
export function TaskDetail({
  task,
  board,
  setUnsaved,
  close,
  openTask,
}: {
  task: Task;
  board: Board;
  setUnsaved: DirtyCallback;
  close: () => void;
  openTask: (id: string) => void;
}) {
  const route = useParams();
  const overview = !route.tab || route.tab === "task";
  const [dirty, setDirty] = useState(false);
  const [retained, setRetained] = useState(false);
  const markDirty = useCallback<DirtyCallback>((value, kept = false) => {
    setDirty(value);
    setRetained(kept);
  }, []);
  const [settingsDirty, setSettingsDirty] = useState(false);
  useEffect(() => {
    setUnsaved(dirty || settingsDirty, retained && !settingsDirty);
    return () => setUnsaved(false);
  }, [dirty, settingsDirty, setUnsaved, retained]);
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={
          <>
            <WorkspaceLink
              className="task-project-breadcrumb"
              to={projectHref(board.project.id)}
            >
              {board.project.name}
            </WorkspaceLink>
            {" / "}
            <span className="task-heading-key">{task.key}</span> {task.title}
          </>
        }
        close={close}
        closeLabel="Back"
        actions={
          !overview && (
            <Button size="sm" variant="outline" asChild>
              <WorkspaceLink to={taskHref(board.project.id, task)}>
                Overview
              </WorkspaceLink>
            </Button>
          )
        }
      >
        <ContentStack className="task-conversation-header">
          <div className="task-header-badges" aria-label="Task labels">
            <MilestoneBadge
              milestone={
                task.publication?.milestone ??
                board.milestones.find((m) => m.id === task.milestone_id)
              }
              preparedRevision={task.publication?.milestone?.revision}
              changed={!!task.milestone_update}
            />
            <DraftBadge publicationStatus={task.publication_status} />
            <TaskLabels ids={task.labels} />
          </div>
          <WorkState state={task.state} />
        </ContentStack>
      </DetailHeader>
      {overview && (
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
          {hasTaskNeeds(task, board.pending_code[task.id], false, true) && (
            <DetailSection title="Waiting on">
              <TaskNeeds
                task={task}
                projectId={board.project.id}
                pendingCode={board.pending_code[task.id]}
                open={openTask}
                detailed
                includeDependencies
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
          <SourceReferences
            documents={task.publication?.documents ?? []}
            updates={task.document_updates ?? []}
          />
          <Disclosure summary="Work details">
            <StageHeader
              projectId={board.project.id}
              task={task}
              refresh={board}
            />
            <WorkspaceLink to={taskHref(board.project.id, task, "history")}>
              Activity and history
            </WorkspaceLink>
          </Disclosure>
        </ContentStack>
      )}
      <TaskConversation
        projectId={board.project.id}
        task={task}
        board={board}
        refresh={board}
        onDirty={markDirty}
        onSettingsDirty={setSettingsDirty}
      />
    </section>
  );
}
