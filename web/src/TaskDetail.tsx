import { Separator } from "@/components/ui/separator";
import { useParams } from "react-router";
import { useContext } from "react";
import { WorkspaceLink } from "./WorkspaceLink";
import { StatusNotice } from "./StatusNotice";
import { ResourceNavigation } from "./WorkspaceTabs";
import { ReviewerSettings } from "./ReviewerSettings";
import { AgentSettingsForm } from "./AgentSettings";
import type { DirtyCallback } from "./retainedState";
import { useCallback, useEffect, useState } from "react";
import { ContentStack, DetailSection } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader, DetailTabs, DraftBadge } from "./Presentation";
import { WorkIdentity } from "./WorkIdentity";
import { taskHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation } from "./TaskConversation";
import { type WorkspaceBoard, type Task } from "./workspace";
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
  board: WorkspaceBoard;
  setUnsaved: DirtyCallback;
  close: () => void;
  openTask: (id: string) => void;
}) {
  const route = useParams();
  const navigate = useContext(ResourceNavigation);
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
          <DraftBadge publicationStatus={task.publication_status} />
          <TaskLabels ids={task.labels} />
        </div>
        <DetailTabs
          id="task"
          title="Task views"
          tabs={["task", "settings"] as const}
          active={settings ? "settings" : "task"}
          change={(tab) =>
            navigate?.(
              tab === "settings"
                ? `${taskHref(workspaceId, task)}/settings`
                : taskHref(workspaceId, task, "task"),
            )
          }
        />
      </ContentStack>
      {!settings && (
        <ContentStack space="section" className="task-context">
          <div
            className="task-definition content-stack"
            aria-label="Current task definition"
          >
            <ExpandableMarkdown key={task.id} title="Definition">
              {task.body}
            </ExpandableMarkdown>
          </div>
          {hasTaskNeeds(task, board.pending_code[task.key], false, true) && (
            <DetailSection title="Waiting on">
              <TaskNeeds
                task={task}
                projectId={workspaceId}
                pendingCode={board.pending_code[task.key]}
                open={openTask}
                detailed
                includeDependencies
                showQuestions={false}
              />
            </DetailSection>
          )}
          {(task.dependents.length > 0 ||
            task.prerequisites.some(
              (p) => !task.blocked_by.some((b) => b.key === p.key),
            )) && (
            <ContentStack aria-label="Related work">
              {task.prerequisites.some(
                (p) => !task.blocked_by.some((b) => b.key === p.key),
              ) && (
                <p>
                  <BlockedBy
                    prefix="Prerequisites:"
                    projectId={workspaceId}
                    tasks={task.prerequisites.filter(
                      (p) => !task.blocked_by.some((b) => b.key === p.key),
                    )}
                    open={openTask}
                  />
                </p>
              )}
              {!!task.dependents.length && (
                <p>
                  <BlockedBy
                    prefix="Needed by:"
                    projectId={workspaceId}
                    tasks={task.dependents}
                    open={openTask}
                  />
                </p>
              )}
            </ContentStack>
          )}
          {task.state?.reason === "scheduling" && (
            <StatusNotice
              title="Task cannot start"
              tone="attention"
              actions={
                <WorkspaceLink to={`/workspaces/${workspaceId}/edit`}>
                  Workspace settings
                </WorkspaceLink>
              }
            >
              <p>{task.state.label}</p>
            </StatusNotice>
          )}
          {!!task.subtasks.length && (
            <DetailSection title="Subtasks">
              <div className="work-resource-list">
                {task.subtasks.map((child) => (
                  <div key={child.id} className="content-stack p-2">
                    <WorkIdentity
                      itemKey={child.key}
                      title={child.title}
                      detail={
                        task.subtasks.some((item) =>
                          item.dependencies?.includes(child.id),
                        )
                          ? "Needed by: " +
                            task.subtasks
                              .filter((item) =>
                                item.dependencies?.includes(child.id),
                              )
                              .map((item) => item.key)
                              .join(", ")
                          : undefined
                      }
                      state={{
                        label: child.status,
                        tone:
                          child.status === "complete"
                            ? "complete"
                            : child.status === "blocked"
                              ? "attention"
                              : child.status === "running"
                                ? "active"
                                : "idle",
                      }}
                    />
                  </div>
                ))}
              </div>
            </DetailSection>
          )}
        </ContentStack>
      )}
      {settings && (
        <ContentStack space="section">
          <DetailSection title="Worker" aria-label="Worker">
            <AgentSettingsForm
              showFast
              workspaceId={workspaceId}
              path={`workspaces/${workspaceId}/tasks/${task.id}/agent-settings`}
              refresh={board}
              onDirty={setSettingsDirty}
            />
          </DetailSection>
          <Separator />
          <DetailSection title="Reviewer">
            <ReviewerSettings
              workspaceId={workspaceId}
              entity={task.id}
              refresh={board}
              onDirty={setReviewerDirty}
            />
          </DetailSection>
        </ContentStack>
      )}
      {!settings && (
        <TaskConversation
          projectId={workspaceId}
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
