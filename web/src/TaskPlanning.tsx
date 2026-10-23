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
  type DragMoveEvent,
} from "@dnd-kit/react";
import { Accessibility, Feedback } from "@dnd-kit/dom";
import { PointerActivationConstraints } from "@dnd-kit/dom";
import { GripVertical } from "lucide-react";
import { reportError } from "./requestFeedback";
import { request, type TaskCard } from "./workspace";

type PlanningStatus = "backlog" | "up_next";
const planning = (status: string): status is PlanningStatus =>
  status === "backlog" || status === "up_next";
const Context = createContext<{
  busy: boolean;
  before: string | null;
}>({
  busy: false,
  before: null,
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
  const [before, setBefore] = useState<string | null>(null);
  function placement(operation: DragMoveEvent["operation"]) {
    const { target, source, position } = operation;
    const status = target?.data.status;
    const element = target?.element;
    if (!element || !planning(status)) return null;
    const bounds = element.getBoundingClientRect();
    const { x, y } = position.current;
    if (
      x < bounds.left ||
      x > bounds.right ||
      y < bounds.top ||
      y > bounds.bottom
    )
      return null;
    const cards = element.querySelectorAll<HTMLElement>("[data-planning-id]");
    const next = Array.from(cards).find((card) => {
      if (card.dataset.planningId === source?.id) return false;
      const rect = card.getBoundingClientRect();
      return y < rect.top + rect.height / 2;
    });
    return { status, before: next?.dataset.planningId ?? null };
  }
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
    <Context.Provider value={{ busy, before }}>
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
              : plugin === Feedback
                ? Feedback.configure({ dropAnimation: null })
                : plugin,
          )
        }
        onDragMove={({ operation }) =>
          setBefore(placement(operation)?.before ?? null)
        }
        onDragOver={({ operation }) =>
          setBefore(placement(operation)?.before ?? null)
        }
        onDragEnd={(event) => {
          setBefore(null);
          if (event.canceled) return;
          const task = tasks.find(
            (item) => item.id === event.operation.source?.id,
          );
          const destination = placement(event.operation);
          if (task && destination)
            void move(task, destination.status, destination.before);
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
    data: { status },
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
  const { busy, before } = useContext(Context);
  const disabled = busy || !planning(task.status);
  const {
    ref: dragRef,
    handleRef,
    isDragging,
  } = useDraggable({ id: task.id, disabled });
  return (
    <div
      ref={dragRef}
      data-planning-id={task.id}
      className={`card-shell relative${planning(task.status) ? " planning-card" : ""}${before === task.id && !isDragging ? " border-t-2 border-primary" : ""}`}
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
