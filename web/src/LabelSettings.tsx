import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { ContentStack, DetailSection } from "./DetailLayout";
import { ConfirmButton } from "./ConfirmButton";
import { TaskLabels } from "./TaskLabels";
import { useLabels, type WorkspaceLabel } from "./labelContext";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { request } from "./workspace";

const colors: WorkspaceLabel["color"][] = [
  "blue",
  "orange",
  "slate",
  "violet",
  "green",
  "red",
  "pink",
  "yellow",
];
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
  function changeDirty(value: boolean) {
    setDirty(value);
    onDirty(value);
  }
  function close() {
    if (dirty && !window.confirm("Discard your unsaved label edits?")) return;
    changeDirty(false);
    setSelected(null);
  }
  return (
    <ContentStack space="section">
      <p>
        Labels help you and the coordinator recognize work. Guidance explains
        when to use each label.
      </p>
      {selected !== null ? (
        <LabelEditor
          key={selected}
          workspaceId={workspaceId}
          incoming={labels.find((item) => item.id === selected)}
          onDirty={changeDirty}
          close={close}
          saved={() => {
            saved();
            setSelected(null);
            changeDirty(false);
          }}
        />
      ) : (
        <DetailSection
          title="Workspace labels"
          actions={
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={labels.filter((item) => !item.deleted_at).length >= 100}
              onClick={() => setSelected("new")}
            >
              New label
            </Button>
          }
        >
          {labels
            .filter((item) => !item.deleted_at)
            .map((item) => (
              <div
                className="flex items-start justify-between gap-4 rounded-md border p-3"
                key={item.id}
              >
                <ContentStack space="tight">
                  <TaskLabels ids={[item.id]} />
                  {item.guidance && (
                    <p className="detail-metadata whitespace-pre-wrap">
                      {item.guidance}
                    </p>
                  )}
                </ContentStack>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`Edit ${item.name} label`}
                  onClick={() => setSelected(item.id)}
                >
                  Edit
                </Button>
              </div>
            ))}
          {!labels.some((item) => !item.deleted_at) && (
            <p className="detail-metadata">No labels yet.</p>
          )}
        </DetailSection>
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
    <section ref={editor} aria-label={loaded ? "Edit label" : "New label"}>
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
            <Label className="field block">
              Color
              <NativeSelect
                aria-label="Color"
                value={values.color}
                onChange={(event) =>
                  change("color", event.target.value as WorkspaceLabel["color"])
                }
              >
                {colors.map((color) => (
                  <option key={color} value={color}>
                    {color[0].toUpperCase() + color.slice(1)}
                  </option>
                ))}
              </NativeSelect>
            </Label>
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
            <div className="actions">
              <Button
                size="sm"
                disabled={!values.name.trim() || (!!loaded && !dirty)}
              >
                {loaded ? "Save label" : "Create label"}
              </Button>
              {loaded && (
                <ConfirmButton
                  size="sm"
                  variant="outline"
                  disabled={dirty}
                  title={`Delete ${loaded.name}?`}
                  description="Remove this label from current tasks? Its past use remains in task history."
                  action={() =>
                    void state.run("DELETE", {
                      expected_revision: loaded.revision,
                      author: "human",
                    })
                  }
                >
                  Delete label
                </ConfirmButton>
              )}
            </div>
          </fieldset>
        </form>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="self-start"
          disabled={busy}
          onClick={close}
        >
          Back to labels
        </Button>
      </ContentStack>
    </section>
  );
}
