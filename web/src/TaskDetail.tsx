import {
  Archive,
  ArchiveRestore,
  Settings,
  FileText,
  Pencil,
} from "lucide-react";
import { HeaderAction } from "./HeaderAction";
import { TaskEditor } from "./TaskEditor";
import { TaskStopAction } from "./TaskStopAction";
import { TaskDeleteAction } from "./TaskDeleteAction";
import { reportError } from "./requestFeedback";
import { request } from "./workspace";
import { useLocation } from "react-router";
import { useContext } from "react";
import { WorkspaceLink } from "./WorkspaceLink";
import { ResourceNavigation } from "./WorkspaceTabs";
import { ReviewerSettings } from "./ReviewerSettings";
import { AgentSettingsForm } from "./AgentSettings";
import type { DirtyCallback } from "./retainedState";
import { useEffect, useState } from "react";
import { ContentStack, DetailHeading, DetailSection } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader } from "./Presentation";
import { WorkIdentity } from "./WorkIdentity";
import { taskHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskConversation } from "./TaskConversation";
import { TaskNotices } from "./TaskNotices";
import { TaskUsage } from "./TaskUsage";
import { type WorkspaceBoard, type Task } from "./workspace";
export function TaskDetail({
  task,
  workspaceId,
  board,
  setUnsaved,
  close,
  openTask,
  navigate: changeLocation,
}: {
  task: Task;
  workspaceId: string;
  board: WorkspaceBoard;
  setUnsaved: DirtyCallback;
  close: () => void;
  openTask: (id: string) => void;
  navigate: (url: string, replace?: boolean) => void;
}) {
  const navigate = useContext(ResourceNavigation);
  const pathname = useLocation().pathname;
  const settings = pathname.endsWith("/settings");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [reviewerDirty, setReviewerDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const archiveLabel = task.archived ? "Restore task" : "Archive task";
  useEffect(() => {
    setUnsaved(settingsDirty || reviewerDirty);
    return () => setUnsaved(false);
  }, [settingsDirty, reviewerDirty, setUnsaved]);
  if (pathname.endsWith("/edit"))
    return (
      <TaskEditor
        task={task}
        workspaceId={workspaceId}
        onDirty={setUnsaved}
        close={close}
        saved={() => changeLocation(taskHref(workspaceId, task), true)}
      />
    );
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={
          <WorkIdentity
            state={task.state}
            itemKey={task.key}
            title={settings ? "Task settings" : "Task"}
          />
        }
        close={close}
        closeLabel="Back"
        actions={
          <>
            <HeaderAction
              label="Edit task"
              disabled={!!task.edit_blocker}
              tooltip={task.edit_blocker ?? "Edit task"}
              onClick={() => navigate?.(`${taskHref(workspaceId, task)}/edit`)}
            >
              <Pencil aria-hidden="true" />
            </HeaderAction>
            <TaskStopAction
              task={task}
              workspaceId={workspaceId}
              busy={busy}
              setBusy={setBusy}
            />
            <HeaderAction
              label={settings ? "View task" : "Task settings"}
              onClick={() =>
                navigate?.(
                  settings
                    ? taskHref(workspaceId, task, "task")
                    : `${taskHref(workspaceId, task)}/settings`,
                )
              }
            >
              {settings ? (
                <FileText aria-hidden="true" />
              ) : (
                <Settings aria-hidden="true" />
              )}
            </HeaderAction>
            <HeaderAction
              label={archiveLabel}
              tooltip={task.archive_blocker || archiveLabel}
              disabled={!!task.archive_blocker || busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await request(
                    `workspaces/${workspaceId}/view/tasks/${task.id}`,
                    "PUT",
                    {
                      expected_revision: task.revision,
                      archived: !task.archived,
                      author: "human",
                    },
                  );
                  close();
                } catch (error) {
                  reportError(
                    error,
                    task.archived
                      ? "Could not restore task"
                      : "Could not archive task",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              {task.archived ? (
                <ArchiveRestore size={16} aria-hidden="true" />
              ) : (
                <Archive size={16} aria-hidden="true" />
              )}
            </HeaderAction>
            <TaskDeleteAction
              task={task}
              workspaceId={workspaceId}
              busy={busy}
              setBusy={setBusy}
              dirty={settingsDirty || reviewerDirty}
              close={close}
            />
          </>
        }
      />
      <ContentStack className="task-summary">
        <DetailHeading
          title={task.title}
          metadata={
            task.project_id && (
              <>
                Project:{" "}
                <WorkspaceLink
                  className="text-inherit"
                  to={`/projects/${task.project_id}/overview`}
                >
                  {board.projects.find(
                    (project) => project.id === task.project_id,
                  )?.name ?? task.project_id}
                </WorkspaceLink>
                {task.publication?.target_branch && (
                  <> · Branch: {task.publication.target_branch}</>
                )}
              </>
            )
          }
        />
        {task.labels.length > 0 && (
          <div className="task-header-badges" aria-label="Task labels">
            <TaskLabels ids={task.labels} />
          </div>
        )}
      </ContentStack>
      {!settings && (
        <ContentStack space="section" className="task-context">
          <TaskNotices
            task={task}
            workspaceId={workspaceId}
            board={board}
            openTask={openTask}
          />
          <div
            className="task-definition content-stack"
            aria-label="Current task definition"
          >
            <ExpandableMarkdown key={task.id} title="Definition">
              {task.body}
            </ExpandableMarkdown>
            {task.acceptance_criteria && (
              <ExpandableMarkdown title="Acceptance criteria">
                {task.acceptance_criteria}
              </ExpandableMarkdown>
            )}
          </div>
          {(task.prerequisites.length > 0 || task.dependents.length > 0) && (
            <ContentStack aria-label="Related work">
              {!!task.prerequisites.length && (
                <p>
                  <BlockedBy
                    prefix="Prerequisites:"
                    projectId={workspaceId}
                    tasks={task.prerequisites}
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
          <TaskUsage
            key={task.id}
            workspaceId={workspaceId}
            taskId={task.id}
            refresh={board}
          />
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
        <ContentStack space="settings">
          <DetailSection settings title="Worker" aria-label="Worker">
            <AgentSettingsForm
              showFast
              workspaceId={workspaceId}
              path={`workspaces/${workspaceId}/tasks/${task.id}/agent-settings`}
              refresh={board}
              onDirty={setSettingsDirty}
            />
          </DetailSection>
          <DetailSection settings title="Reviewer">
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
        <DetailSection title="Task feed">
          <TaskConversation
            projectId={workspaceId}
            task={task}
            board={board}
            refresh={board}
          />
        </DetailSection>
      )}
    </section>
  );
}
