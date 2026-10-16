import { useParams } from "react-router";
import { useContext } from "react";
import { ResourceNavigation } from "./WorkspaceTabs";
import { ReviewerSettings } from "./ReviewerSettings";
import { SourceReferences } from "./SourceReferences";
import type { DirtyCallback } from "./retainedState";
import { useCallback, useEffect, useState } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader, DetailTabs, DraftBadge } from "./Presentation";
import { MilestoneBadge } from "./MilestoneBadge";
import { WorkIdentity } from "./WorkIdentity";
import { taskHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation, StageHeader } from "./TaskConversation";
import { AgentPermissions } from "./AgentPermissions";
import { TaskReview } from "./TaskReview";
import { type Board, type Task } from "./workspace";
export function TaskDetail({
  task,
  workspaceId,
  board,
  setUnsaved,
  close,
  openTask,
}: {
  task: Task;
  workspaceId: string;
  board: Board;
  setUnsaved: DirtyCallback;
  close: () => void;
  openTask: (id: string) => void;
}) {
  const route = useParams();
  const navigate = useContext(ResourceNavigation);
  const overview = !route.tab || route.tab === "task";
  const settings = route.tab === "settings";
  const [dirty, setDirty] = useState(false);
  const [retained, setRetained] = useState(false);
  const markDirty = useCallback<DirtyCallback>((value, kept = false) => {
    setDirty(value);
    setRetained(kept);
  }, []);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [reviewerDirty, setReviewerDirty] = useState(false);
  useEffect(() => {
    setUnsaved(
      dirty || settingsDirty || reviewerDirty,
      retained && !settingsDirty && !reviewerDirty,
    );
    return () => setUnsaved(false);
  }, [dirty, settingsDirty, reviewerDirty, setUnsaved, retained]);
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={
          <WorkIdentity
            state={task.state}
            itemKey={task.key}
            title={task.title}
          />
        }
        close={close}
        closeLabel="Back"
      />
      <ContentStack space="section" className="task-view-navigation">
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
        <DetailTabs
          id="task"
          title="Task views"
          tabs={["overview", "activity", "settings"] as const}
          active={overview ? "overview" : settings ? "settings" : "activity"}
          change={(tab) =>
            navigate?.(
              tab === "settings"
                ? `${taskHref(board.project.id, task)}/settings`
                : taskHref(
                    board.project.id,
                    task,
                    tab === "overview" ? "task" : "history",
                  ),
            )
          }
        />
      </ContentStack>
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
          <TaskReview
            projectId={board.project.id}
            taskId={task.id}
            workspaceId={workspaceId}
            refresh={board}
            compact
          />
        </ContentStack>
      )}
      {settings && (
        <ReviewerSettings
          workspaceId={workspaceId}
          purpose="task"
          projectId={board.project.id}
          entity={task.id}
          refresh={board}
          onDirty={setReviewerDirty}
        />
      )}
      {!overview && !settings && (
        <Disclosure summary="Task details">
          <ContentStack space="section">
            {(task.prerequisites.some(
              (p) => !task.blocked_by.some((b) => b.key === p.key),
            ) ||
              task.dependents.length > 0) && (
              <DetailSection title="Related work">
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
              </DetailSection>
            )}
            <SourceReferences
              documents={task.publication?.documents ?? []}
              updates={task.document_updates ?? []}
            />
            <DetailSection title="Progress">
              <StageHeader
                projectId={board.project.id}
                task={task}
                refresh={board}
              />
            </DetailSection>
            <TaskReview
              projectId={board.project.id}
              taskId={task.id}
              workspaceId={workspaceId}
              refresh={board}
            />
          </ContentStack>
        </Disclosure>
      )}
      {!settings && (
        <TaskConversation
          projectId={board.project.id}
          task={task}
          board={board}
          refresh={board}
          onDirty={markDirty}
          onSettingsDirty={setSettingsDirty}
        />
      )}
    </section>
  );
}
