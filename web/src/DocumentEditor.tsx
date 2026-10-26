import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { components } from "./api-schema";
import { ContentStack } from "./DetailLayout";
import { DocumentUsageFields, usageFields } from "./DocumentUsage";
import { MarkdownField } from "./Markdown";
import { DetailHeader } from "./Presentation";
import { ResourceRetry } from "./ResourceRetry";
import type { DirtyCallback } from "./retainedState";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { useResource } from "./useResource";
import { request } from "./workspace";

type Settings = components["schemas"]["DocumentSettings"];
const fields = (record?: Settings) => ({
  title: record?.title ?? "",
  body: record?.body ?? "",
  usage: usageFields(record?.usage),
});

export function DocumentEditor({
  path,
  refresh,
  onDirty,
  saved,
  close,
}: {
  path: string;
  refresh: unknown;
  onDirty: DirtyCallback;
  saved: (record: Settings) => void;
  close: () => void;
}) {
  const resource = useResource<Settings>(`${path}/settings`, refresh);
  const state = useRecordEditor({
    incoming: resource.data ?? undefined,
    fields,
    onDirty,
    recordName: "document",
    retainedKey: `${path}:settings`,
    path: () => `${path}/settings`,
    version: (record) => JSON.stringify([record.revision, record.usage]),
    read: async (signal) => {
      const fresh = await request<Settings>(
        `${path}/settings`,
        "GET",
        undefined,
        signal,
      );
      if (signal.aborted)
        throw new DOMException("Read cancelled", "AbortError");
      resource.invalidate();
      resource.setData(fresh);
      return fresh;
    },
    saved: (record, method) => {
      resource.invalidate();
      resource.setData(record);
      if (method === "PUT") saved(record);
    },
  });
  const { loaded, values, dirty, busy, change, element: editor } = state;
  return (
    <section ref={editor} className="editor" aria-label="Edit document">
      <DetailHeader title="Edit document" close={close} />
      <ContentStack space="section">
        <ResourceRetry resources={[resource]} />
        {!loaded && resource.loading && <p>Loading document…</p>}
        <EditorFeedback state={state} />
        {loaded && (
          <form
            className="content-stack"
            data-space="section"
            onSubmit={(event) => {
              event.preventDefault();
              const usageChanged =
                JSON.stringify(values.usage) !==
                JSON.stringify(usageFields(loaded.usage));
              void state.run("PUT", {
                title: values.title,
                body: values.body,
                expected_revision: loaded.revision,
                author: "human",
                ...(usageChanged && loaded.id !== "guidance"
                  ? {
                      usage: {
                        expected_revision: loaded.usage.revision,
                        projectless: values.usage.projectless,
                        projects: values.usage.projects.flatMap((project) => {
                          const original = loaded.usage.projects.find(
                            (item) => item.id === project.id,
                          )!;
                          return original.purpose === project.purpose
                            ? []
                            : [
                                {
                                  ...project,
                                  expected_revision: original.revision,
                                },
                              ];
                        }),
                      },
                    }
                  : {}),
              });
            }}
          >
            <fieldset
              disabled={busy || !!loaded.deleted_at}
              className="content-stack"
              data-space="section"
            >
              <Label className="field block">
                Document title
                <Input
                  required
                  maxLength={200}
                  value={values.title}
                  onChange={(event) => change("title", event.target.value)}
                />
              </Label>
              <MarkdownField
                label="Content"
                rows={16}
                value={values.body}
                onChange={(value) => change("body", value)}
              />
              <DocumentUsageFields
                usage={loaded.usage}
                value={values.usage}
                change={(value) => change("usage", value)}
                guidance={loaded.id === "guidance"}
              />
            </fieldset>
            <div className="actions">
              <Button
                size="sm"
                disabled={
                  busy || !!loaded.deleted_at || !values.title.trim() || !dirty
                }
              >
                Save document
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => state.cancel(close)}
              >
                Cancel
              </Button>
            </div>
            {loaded.deleted_at && (
              <p role="status">
                This document was deleted. Restore a revision to use it again.
              </p>
            )}
          </form>
        )}
      </ContentStack>
    </section>
  );
}
