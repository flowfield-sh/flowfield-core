import { reportError } from "./requestFeedback";
import { useEffect, useState, useRef, type ReactNode } from "react";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { DetailHeader, DraftBadge, TaskTypeBadge } from "./Presentation";
import { MilestoneBadge } from "./MilestoneBadge";
import { ExpandableMarkdown } from "./ExpandableMarkdown";
import { BlockedBy } from "./TaskLinks";
import { TaskNeeds, TaskState, hasTaskNeeds } from "./TaskNeeds";
import { TaskConversation, StageHeader } from "./TaskConversation";
import { Button } from "@/components/ui/button";
import { ActionTooltip } from "./ActionTooltip";
import { ConfirmButton } from "./ConfirmButton";
import { AgentPermissions } from "./AgentPermissions";
import { label, request, type Board, type Task } from "./workspace";
export function TaskDetail({
  task,
  board,
  path,
  setUnsaved,
  saved,
  close,
  openTask,
  priorityControls,
}: {
  task: Task;
  board: Board;
  path: string;
  setUnsaved: (dirty: boolean) => void;
  saved: (task: Task) => void;
  close: () => void;
  openTask: (id: string) => void;
  priorityControls: ReactNode;
}) {
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [dirty, setDirty] = useState(false);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setUnsaved(dirty || settingsDirty);
    return () => setUnsaved(false);
  }, [dirty, settingsDirty, setUnsaved]);
  const archiveReason = dirty
    ? "Send or discard your message before archiving."
    : settingsDirty
      ? "Save or discard worker settings before archiving."
      : task.archive_blocker;
  async function archive() {
    setBusy(true);
    try {
      const result = await request<Task>(
        path + "/view/tasks/" + task.id,
        "PUT",
        {
          expected_revision: task.revision,
          archived: !task.archived,
          author: "human",
        },
      );
      if (alive.current) saved(result);
    } catch (e) {
      if (alive.current) reportError(e, "Could not update task");
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section className="editor task-detail">
      <DetailHeader
        title={task.key + " · " + task.title}
        close={close}
        closeLabel="Back to board"
      >
        <div className="task-conversation-header content-stack">
          <div className="entity-labels">
            <TaskTypeBadge type={task.task_type} />
            <DraftBadge publicationStatus={task.publication_status} />
            <MilestoneBadge
              milestone={board.milestones.find(
                (m) => m.id === task.milestone_id,
              )}
            />
          </div>
          <TaskState
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
          <StageHeader
            projectId={board.project.id}
            task={task}
            refresh={board}
          />
        </div>
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
          (p) => !task.blocked_by.some((b) => b.id === p.id),
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
        taskActions={
          <>
            {priorityControls}
            <ActionTooltip label={archiveReason} disabled>
              {task.archived ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !!archiveReason}
                  onClick={() => void archive()}
                >
                  Restore
                </Button>
              ) : (
                <ConfirmButton
                  size="sm"
                  variant="outline"
                  disabled={busy || !!archiveReason}
                  title={`Archive ${task.key}?`}
                  description="Remove this task from the board? Its status stays the same. Restore it from Archive anytime."
                  action={() => void archive()}
                >
                  Archive
                </ConfirmButton>
              )}
            </ActionTooltip>
          </>
        }
      />
    </section>
  );
}
