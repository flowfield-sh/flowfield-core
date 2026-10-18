import { useParams } from "react-router";
import { useContext } from "react";
import { ResourceNavigation } from "./WorkspaceTabs";
import { ReviewerSettings } from "./ReviewerSettings";
import { AgentSettingsControl } from "./AgentSettings";
import { choiceLabel } from "./HarnessModels";
import type { components } from "./api-schema";
import type { DirtyCallback } from "./retainedState";
import { useCallback, useEffect, useState } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader, DetailTabs, DraftBadge } from "./Presentation";
import { WorkIdentity } from "./WorkIdentity";
import { taskHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation } from "./TaskConversation";
import { AgentPermissions } from "./AgentPermissions";
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
  const [modelOpen, setModelOpen] = useState(false);
  const [choice, setChoice] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
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
          <AgentPermissions
            workspaceId={workspaceId}
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
          {!!task.subtasks.length && (
            <Disclosure summary="Work">
              <div className="work-resource-list">
                {task.subtasks.map((child) => (
                  <div key={child.id} className="content-stack p-2">
                    <WorkIdentity
                      itemKey={child.key}
                      title={child.title}
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
                    {!!(child.dependencies ?? []).length && (
                      <p className="text-xs text-muted-foreground">
                        After{" "}
                        {(child.dependencies ?? [])
                          .map(
                            (id) =>
                              task.subtasks.find((item) => item.id === id)?.key,
                          )
                          .join(", ")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </Disclosure>
          )}
        </ContentStack>
      )}
      {settings && (
        <ContentStack space="section">
          <DetailSection title="Worker">
            <AgentSettingsControl
              workspaceId={workspaceId}
              path={`workspaces/${workspaceId}/tasks/${task.id}/agent-settings`}
              refresh={board}
              open={modelOpen}
              onOpenChange={setModelOpen}
              onDirty={setSettingsDirty}
              onReady={setChoice}
              label={choice ? choiceLabel(choice) : "Worker settings"}
            />
          </DetailSection>
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
