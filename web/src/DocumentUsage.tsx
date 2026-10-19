import { WorkspaceLink } from "./WorkspaceLink";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import type { components } from "./api-schema";
import { ContentStack, DetailSection } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ResourceRetry } from "./ResourceRetry";
import type { DirtyCallback } from "./retainedState";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import { useResource } from "./useResource";

type Usage = components["schemas"]["DocumentUsageView"];
type Purpose = NonNullable<Usage["projectless"]>;
const fields = (record?: Usage) => ({
  projectless: record?.projectless ?? null,
  projects: record?.projects.map(({ id, purpose }) => ({ id, purpose })) ?? [],
});

export function DocumentUsage({
  path,
  refresh,
  onDirty,
  editing,
}: {
  path: string;
  refresh: unknown;
  onDirty: DirtyCallback;
  editing: boolean;
}) {
  const resource = useResource<Usage>(path, refresh);
  const state = useRecordEditor({
    incoming: resource.data ?? undefined,
    fields,
    path: () => path,
    saved: (value) => resource.setData(value),
    onDirty,
    recordName: "document usage",
    retainedKey: path,
    version: (value) => JSON.stringify(value),
  });
  return (
    <ContentStack>
      <ResourceRetry resources={[resource]} />
      {state.loaded && (
        <ContentStack space="tight">
          <p className="document-projects">
            Projects:{" "}
            {state.loaded.projects
              .filter((p) => p.purpose)
              .map((p, i) => (
                <span key={p.id}>
                  {i > 0 && ", "}
                  <WorkspaceLink to={`/projects/${p.id}/overview`}>
                    {p.name}
                  </WorkspaceLink>
                </span>
              ))}
            {!state.loaded.projects.some((p) => p.purpose) && "None"}
          </p>
          <p className="document-projects">
            {state.loaded.projectless === "required"
              ? "Required reading for tasks without a project."
              : state.loaded.projectless === "reference"
                ? "Reference for tasks without a project."
                : "Not included in tasks without a project."}
          </p>
        </ContentStack>
      )}
      {editing && (
        <DetailSection title="Use this document">
          <ContentStack space="section">
            <p>
              Defaults for future tasks. Prepared work keeps its saved sources.
            </p>
            <EditorFeedback state={state} />
            {state.loaded && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void state.run("PUT", {
                    expected_revision: state.loaded!.revision,
                    projectless: state.values.projectless,
                    projects: state.values.projects.flatMap((project) => {
                      const original = state.loaded!.projects.find(
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
                    author: "human",
                  });
                }}
              >
                <fieldset
                  disabled={state.busy}
                  className="content-stack"
                  data-space="section"
                >
                  <ContentStack>
                    <SettingsCheckbox
                      checked={state.values.projectless !== null}
                      onChange={(event) =>
                        state.change(
                          "projectless",
                          event.target.checked ? "required" : null,
                        )
                      }
                    >
                      Include for tasks without a project
                    </SettingsCheckbox>
                    {state.values.projectless && (
                      <PurposeSelect
                        label="Use for tasks without a project as"
                        value={state.values.projectless}
                        change={(value) => state.change("projectless", value)}
                      />
                    )}
                  </ContentStack>
                  {state.loaded.projects.map((project) => {
                    const value =
                      state.values.projects.find(
                        (item) => item.id === project.id,
                      )?.purpose ?? null;
                    const change = (purpose: Purpose | null) =>
                      state.change(
                        "projects",
                        state.values.projects.map((item) =>
                          item.id === project.id ? { ...item, purpose } : item,
                        ),
                      );
                    return (
                      <ContentStack key={project.id}>
                        <SettingsCheckbox
                          checked={value !== null}
                          onChange={(event) =>
                            change(event.target.checked ? "required" : null)
                          }
                        >
                          {project.name}
                        </SettingsCheckbox>
                        {value && (
                          <PurposeSelect
                            label={`Use for ${project.name} as`}
                            value={value}
                            change={change}
                          />
                        )}
                      </ContentStack>
                    );
                  })}
                  <div className="actions">
                    <Button size="sm" disabled={!state.dirty}>
                      Save usage
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!state.dirty}
                      onClick={state.cancel}
                    >
                      Cancel
                    </Button>
                  </div>
                </fieldset>
              </form>
            )}
          </ContentStack>
        </DetailSection>
      )}
    </ContentStack>
  );
}

function PurposeSelect({
  label,
  value,
  change,
}: {
  label: string;
  value: Purpose;
  change: (value: Purpose) => void;
}) {
  return (
    <label className="field block">
      <span className="detail-metadata">{label}</span>
      <NativeSelect
        value={value}
        onChange={(event) => change(event.target.value as Purpose)}
      >
        <option value="required">Required source</option>
        <option value="reference">Reference material</option>
      </NativeSelect>
    </label>
  );
}
