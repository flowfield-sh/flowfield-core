import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Archive,
  ArchiveRestore,
  Settings,
  FileText,
  FileOutput,
} from "lucide-react";
import { HeaderAction } from "./HeaderAction";
import { TaskDeleteAction } from "./TaskDeleteAction";
import { reportError } from "./requestFeedback";
import { request } from "./workspace";
import { useLocation } from "react-router";
import { useContext } from "react";
import { WorkspaceLink } from "./WorkspaceLink";
import { StatusNotice } from "./StatusNotice";
import { ResourceNavigation } from "./WorkspaceTabs";
import { ReviewerSettings } from "./ReviewerSettings";
import { AgentSettingsForm } from "./AgentSettings";
import type { DirtyCallback } from "./retainedState";
import { useEffect, useState } from "react";
import { ContentStack, DetailHeading, DetailSection } from "./DetailLayout";
import { TaskLabels } from "./TaskLabels";
import { DetailHeader, DraftBadge } from "./Presentation";
import { WorkIdentity } from "./WorkIdentity";
import { taskHref, resultHref } from "./navigation";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation } from "./TaskConversation";
import { TaskReview } from "./TaskReview";
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
  const navigate = useContext(ResourceNavigation);
  const settings = useLocation().pathname.endsWith("/settings");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [reviewerDirty, setReviewerDirty] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const archiveLabel = task.archived ? "Restore task" : "Archive task";
  useEffect(() => {
    setUnsaved(settingsDirty || reviewerDirty);
    return () => setUnsaved(false);
  }, [settingsDirty, reviewerDirty, setUnsaved]);
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={
          <WorkIdentity
            state={task.state}
            itemKey={task.key}
            keyBadge={
              <DraftBadge publicationStatus={task.publication_status} />
            }
            title="Task"
          />
        }
        close={close}
        closeLabel="Back"
        actions={
          <>
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
              disabled={!!task.archive_blocker || archiving}
              onClick={async () => {
                setArchiving(true);
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
                  setArchiving(false);
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
              busy={archiving}
              setBusy={setArchiving}
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
          {task.latest_result_id && (
            <div className="actions">
              <Button variant="outline" size="sm" asChild>
                <WorkspaceLink
                  to={resultHref(workspaceId, task.latest_result_id)}
                >
                  <FileOutput aria-hidden="true" /> View latest result
                </WorkspaceLink>
              </Button>
            </div>
          )}
          <div
            className="task-definition content-stack"
            aria-label="Current task definition"
          >
            <ExpandableMarkdown key={task.id} title="Definition">
              {task.body}
            </ExpandableMarkdown>
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
          <TaskReview
            projectId={workspaceId}
            taskId={task.id}
            taskKey={task.key}
            refresh={board}
            guidanceOnly
          />
          {hasTaskNeeds(
            task,
            board.pending_code[task.key],
            false,
            false,
            true,
          ) && (
            <DetailSection title="Waiting on">
              <TaskNeeds
                task={task}
                projectId={workspaceId}
                pendingCode={board.pending_code[task.key]}
                open={openTask}
                detailed
                showQuestions={false}
                hideDependencies
              />
            </DetailSection>
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
