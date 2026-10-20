import { useId, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { ChoiceSelect } from "@/components/ui/choice-select";
import { ContentStack, DetailSection } from "./DetailLayout";
import { ConfirmButton } from "./ConfirmButton";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { labelColors, labelPalette } from "./labelColors";
import type { WorkspaceLabel } from "./labelContext";

const fields = (label?: WorkspaceLabel) => ({
  name: label?.name ?? "",
  color: label?.color ?? "slate",
  guidance: label?.guidance ?? "",
});

export function WorkspaceLabels({
  workspaceId,
  refresh,
  onDirty,
}: {
  workspaceId: string;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
}) {
  const path = `workspaces/${workspaceId}/labels`;
  const labels = useResource<WorkspaceLabel[]>(path, refresh);
  const [editing, setEditing] = useState<string | null>(null);
  return (
    <DetailSection
      title="Labels"
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={editing !== null}
          onClick={() => setEditing("new")}
        >
          Add label
        </Button>
      }
    >
      <ResourceRetry resources={[labels]} />
      {!labels.data && !labels.error && <p>Loading labels…</p>}
      <ul className="divide-y rounded-md border" aria-label="Workspace labels">
        {editing === "new" && (
          <li className="px-3 py-2">
            <LabelEditor
              path={path}
              onDirty={onDirty}
              close={() => {
                setEditing(null);
                onDirty(false);
              }}
              saved={() => {
                labels.retry();
                setEditing(null);
                onDirty(false);
              }}
            />
          </li>
        )}
        {labels.data
          ?.filter((label) => !label.deleted_at || editing === label.id)
          .map((label) => (
            <li key={label.id} className="px-3 py-2">
              {editing === label.id ? (
                <LabelEditor
                  incoming={label}
                  path={path}
                  onDirty={onDirty}
                  close={() => {
                    setEditing(null);
                    onDirty(false);
                  }}
                  saved={() => {
                    labels.retry();
                    setEditing(null);
                    onDirty(false);
                  }}
                />
              ) : (
                <div className="flex items-center gap-3">
                  <div
                    className="min-w-0 flex-1 content-stack"
                    data-space="tight"
                  >
                    <div>
                      <Badge
                        variant="outline"
                        className={labelColors[label.color].className}
                      >
                        {label.name}
                      </Badge>
                    </div>
                    {label.guidance && (
                      <p className="detail-metadata">{label.guidance}</p>
                    )}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={editing !== null}
                    onClick={() => setEditing(label.id)}
                    aria-label={`Edit ${label.name}`}
                  >
                    Edit
                  </Button>
                </div>
              )}
            </li>
          ))}
      </ul>
    </DetailSection>
  );
}

function LabelEditor({
  incoming,
  path,
  onDirty,
  close,
  saved,
}: {
  incoming?: WorkspaceLabel;
  path: string;
  onDirty: (dirty: boolean) => void;
  close: () => void;
  saved: () => void;
}) {
  const fieldId = useId();
  const state = useRecordEditor<WorkspaceLabel, ReturnType<typeof fields>>({
    incoming,
    fields,
    path: (record) => path + (record ? `/${record.id}` : ""),
    recordName: "label",
    onDirty,
    saved,
    read: async () => {
      const values = await request<WorkspaceLabel[]>(path);
      const value = values.find((item) => item.id === incoming?.id);
      if (!value) throw new Error("This label is no longer available.");
      return value;
    },
  });
  const { values, busy, dirty, change, run, loaded } = state;
  return (
    <ContentStack>
      <EditorFeedback state={state} />
      {incoming?.deleted_at && <p role="status">This label was deleted.</p>}
      <form
        className="content-stack"
        aria-label={incoming ? `Edit ${incoming.name}` : "New label"}
        onSubmit={(event) => {
          event.preventDefault();
          void run(loaded ? "PUT" : "POST", {
            ...values,
            ...(loaded ? { expected_revision: loaded.revision } : {}),
            author: "human",
          });
        }}
      >
        <fieldset
          disabled={busy || !!incoming?.deleted_at}
          className="content-stack"
        >
          <div className="field">
            <Label htmlFor={fieldId + "-name"}>Name</Label>
            <Input
              id={fieldId + "-name"}
              required
              maxLength={40}
              value={values.name}
              onChange={(e) => change("name", e.target.value)}
            />
          </div>
          <div className="field">
            <Label>Color</Label>
            <ChoiceSelect
              label="Label color"
              value={values.color}
              options={labelPalette.map((color) => ({
                value: color,
                label: (
                  <Badge
                    variant="outline"
                    className={labelColors[color].className}
                  >
                    {labelColors[color].name}
                  </Badge>
                ),
              }))}
              onChange={(color) =>
                change("color", color as WorkspaceLabel["color"])
              }
            />
          </div>
          <div className="field">
            <Label htmlFor={fieldId + "-guidance"}>Guidance</Label>
            <Textarea
              id={fieldId + "-guidance"}
              rows={2}
              maxLength={1000}
              value={values.guidance}
              onChange={(e) => change("guidance", e.target.value)}
            />
          </div>
        </fieldset>
        <div className="actions">
          <Button size="sm" disabled={busy || !dirty || !!incoming?.deleted_at}>
            {busy ? "Saving…" : loaded ? "Save label" : "Create label"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (state.cancel()) close();
            }}
          >
            Cancel
          </Button>
          {loaded && (
            <ConfirmButton
              size="sm"
              variant="outline"
              disabled={busy || !!incoming?.deleted_at}
              title={`Delete ${loaded.name}?`}
              description="Remove this label from current tasks. Historical assignments are kept."
              action={() =>
                void run("DELETE", {
                  expected_revision: loaded.revision,
                  author: "human",
                })
              }
            >
              Delete label
            </ConfirmButton>
          )}
        </div>
      </form>
    </ContentStack>
  );
}
