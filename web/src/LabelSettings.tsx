import { useState } from "react";
import { Check, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ContentStack } from "./DetailLayout";
import { ConfirmButton } from "./ConfirmButton";
import { TaskLabels } from "./TaskLabels";
import { labelColors } from "./labelColors";
import { useLabels, type WorkspaceLabel } from "./labelContext";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { reportError } from "./requestFeedback";
import { request } from "./workspace";

const fields = (record?: WorkspaceLabel) => ({
  name: record?.name ?? "",
  color: record?.color ?? "slate",
  guidance: record?.guidance ?? "",
});

export function LabelSettings({
  workspaceId,
  onDirty,
  saved,
}: {
  workspaceId: string;
  onDirty: (dirty: boolean) => void;
  saved: () => void;
}) {
  const labels = useLabels();
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  function changeDirty(value: boolean) {
    setDirty(value);
    onDirty(value);
  }
  function select(value: string | null) {
    if (dirty && !window.confirm("Discard your unsaved label edits?")) return;
    changeDirty(false);
    setSelected(value);
  }
  function editor(identity: string) {
    return (
      <LabelEditor
        key={identity}
        workspaceId={workspaceId}
        incoming={labels.find((item) => item.id === identity)}
        onDirty={changeDirty}
        close={() => select(null)}
        saved={() => {
          saved();
          setSelected(null);
          changeDirty(false);
        }}
      />
    );
  }
  return (
    <ContentStack space="section">
      <div className="actions justify-end">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={
            selected === "new" ||
            !!deleting ||
            labels.filter((item) => !item.deleted_at).length >= 100
          }
          onClick={() => select("new")}
        >
          New label
        </Button>
      </div>
      <div className="label-list">
        {labels
          .filter((item) => !item.deleted_at || item.id === selected)
          .map((item) => (
            <div className="label-list-item" key={item.id}>
              <div className="label-list-row">
                <TaskLabels ids={[item.id]} />
                <div className="actions">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Edit ${item.name} label`}
                        disabled={!!deleting || !!item.deleted_at}
                        onClick={() => select(item.id)}
                      >
                        <Pencil />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Edit label</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex">
                        <ConfirmButton
                          size="icon-sm"
                          variant="ghost"
                          aria-label={`Delete ${item.name} label`}
                          disabled={!!deleting || dirty || !!item.deleted_at}
                          title={`Delete ${item.name}?`}
                          description="Remove this label from current tasks? Its past use remains in task history."
                          action={() => {
                            setDeleting(item.id);
                            void request(
                              `workspaces/${workspaceId}/labels/${item.id}`,
                              "DELETE",
                              {
                                expected_revision: item.revision,
                                author: "human",
                              },
                            )
                              .then(() => {
                                if (selected === item.id) select(null);
                                saved();
                              })
                              .catch((error) =>
                                reportError(error, "Could not delete label"),
                              )
                              .finally(() => setDeleting(null));
                          }}
                        >
                          <Trash2 />
                          <span className="sr-only">Delete label</span>
                        </ConfirmButton>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>Delete label</TooltipContent>
                  </Tooltip>
                </div>
              </div>
              {selected === item.id && editor(item.id)}
            </div>
          ))}
        {selected === "new" && (
          <div className="label-list-item">{editor("new")}</div>
        )}
      </div>
      {!labels.some((item) => !item.deleted_at) && selected !== "new" && (
        <p>No labels yet.</p>
      )}
    </ContentStack>
  );
}

function LabelEditor({
  workspaceId,
  incoming,
  onDirty,
  saved,
  close,
}: {
  workspaceId: string;
  incoming?: WorkspaceLabel;
  onDirty: (dirty: boolean) => void;
  saved: () => void;
  close: () => void;
}) {
  const path = `workspaces/${workspaceId}/labels`;
  const state = useRecordEditor({
    incoming,
    fields,
    recordName: "label",
    onDirty,
    saved: (_record, method) => {
      if (method !== "GET") saved();
    },
    path: (record) => `${path}${record ? `/${record.id}` : ""}`,
    read: async () => {
      const labels = await request<WorkspaceLabel[]>(path);
      const record = labels.find((item) => item.id === incoming?.id);
      if (!record) throw new Error("Label no longer exists.");
      return record;
    },
  });
  const { loaded, values, busy, dirty, change, element: editor } = state;
  return (
    <section
      ref={editor}
      className="label-editor"
      aria-label={loaded ? "Edit label" : "New label"}
    >
      <ContentStack space="section">
        <EditorFeedback state={state} />
        {incoming?.deleted_at && (
          <p role="status">
            This label was deleted. Its past use is preserved in task history.
          </p>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void state.run(loaded ? "PUT" : "POST", {
              ...values,
              ...(loaded ? { expected_revision: loaded.revision } : {}),
              author: "human",
            });
          }}
        >
          <fieldset
            disabled={busy || !!incoming?.deleted_at}
            className="content-stack"
            data-space="section"
          >
            <Label className="field block">
              Label name
              <Input
                required
                maxLength={40}
                value={values.name}
                onChange={(event) => change("name", event.target.value)}
              />
            </Label>
            <fieldset className="content-stack" data-space="tight">
              <legend className="field">Color</legend>
              <div className="label-color-grid" role="group" aria-label="Color">
                {Object.entries(labelColors).map(([color, value]) => (
                  <Tooltip key={color}>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="outline"
                        size="icon-sm"
                        className={`w-full min-w-0 ${value.className} ${values.color === color ? "ring-2 ring-foreground ring-offset-1 ring-offset-background" : ""}`}
                        aria-label={value.name}
                        aria-pressed={values.color === color}
                        onClick={() =>
                          change("color", color as WorkspaceLabel["color"])
                        }
                      >
                        {values.color === color && <Check />}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{value.name}</TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </fieldset>
            <Label className="field block">
              Guidance
              <Textarea
                aria-label="Guidance"
                maxLength={1000}
                rows={3}
                value={values.guidance}
                onChange={(event) => change("guidance", event.target.value)}
              />
            </Label>
            <div className="actions justify-end">
              <Button
                size="sm"
                disabled={!values.name.trim() || (!!loaded && !dirty)}
              >
                {loaded ? "Save label" : "Create label"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={close}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
