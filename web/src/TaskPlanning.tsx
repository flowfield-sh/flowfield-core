import {
  createContext,
  useContext,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import {
  DragDropProvider,
  PointerSensor,
  useDraggable,
  useDroppable,
} from "@dnd-kit/react";
import { Accessibility } from "@dnd-kit/dom";
import { PointerActivationConstraints } from "@dnd-kit/dom";
import { GripVertical } from "lucide-react";
import { reportError } from "./requestFeedback";
import { request, type TaskCard } from "./workspace";

type PlanningStatus = "backlog" | "up_next";
const planning = (status: string): status is PlanningStatus =>
  status === "backlog" || status === "up_next";
const Context = createContext<{
  busy: boolean;
}>({
  busy: false,
});

export function TaskPlanning({
  tasks,
  children,
}: {
  tasks: TaskCard[];
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  async function move(
    task: TaskCard,
    status: PlanningStatus,
    before: string | null,
  ) {
    if (saving.current || !planning(task.status) || task.id === before) return;
    saving.current = true;
    setBusy(true);
    try {
      await request(
        `workspaces/${task.workspace_id}/tasks/${task.id}/prioritize`,
        "POST",
        {
          expected_revision: task.revision,
          status,
          before_id: before,
          author: "human",
        },
      );
    } catch (error) {
      reportError(error, "Could not move task");
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  return (
    <Context.Provider value={{ busy }}>
      <DragDropProvider
        sensors={[
          PointerSensor.configure({
            activationConstraints: [
              new PointerActivationConstraints.Distance({ value: 6 }),
            ],
          }),
        ]}
        plugins={(defaults) =>
          defaults.map((plugin) =>
            plugin === Accessibility
              ? Accessibility.configure({
                  screenReaderInstructions: { draggable: "" },
                })
              : plugin,
          )
        }
        onDragEnd={(event) => {
          if (event.canceled) return;
          const { source, target } = event.operation;
          const task = tasks.find((item) => item.id === source?.id);
          const status = target?.data.status;
          if (task && typeof status === "string" && planning(status))
            void move(task, status, target?.data.before ?? null);
        }}
      >
        {children}
      </DragDropProvider>
    </Context.Provider>
  );
}

export function PlanningColumn({
  status,
  ...props
}: ComponentProps<"div"> & { status: string }) {
  const { busy } = useContext(Context);
  const { ref, isDropTarget } = useDroppable({
    id: `column:${status}`,
    disabled: busy || !planning(status),
    data: { status, before: null },
  });
  return <div ref={ref} data-drop-target={isDropTarget} {...props} />;
}

export function PlanningCard({
  task,
  children,
}: {
  task: TaskCard;
  children: ReactNode;
}) {
  if (!planning(task.status))
    return <div className="card-shell">{children}</div>;
  return <MovableCard task={task}>{children}</MovableCard>;
}

function MovableCard({
  task,
  children,
}: {
  task: TaskCard;
  children: ReactNode;
}) {
  const { busy } = useContext(Context);
  const disabled = busy || !planning(task.status);
  const {
    ref: dragRef,
    handleRef,
    isDragging,
  } = useDraggable({ id: task.id, disabled });
  const { ref: dropRef, isDropTarget } = useDroppable({
    id: `before:${task.id}`,
    disabled,
    data: { status: task.status, before: task.id },
  });
  return (
    <div
      ref={(node) => {
        dragRef(node);
        dropRef(node);
      }}
      className={`card-shell relative${planning(task.status) ? " planning-card" : ""}${isDropTarget && !isDragging ? " border-t-2 border-primary" : ""}`}
      data-dragging={isDragging}
    >
      {children}
      {planning(task.status) && (
        <button
          type="button"
          ref={handleRef}
          className="absolute right-1 top-1 z-10 flex size-8 cursor-grab items-center justify-center rounded-md text-muted-foreground active:cursor-grabbing disabled:pointer-events-none disabled:opacity-50"
          aria-label={`Move ${task.key}`}
          disabled={busy}
        >
          <GripVertical size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
