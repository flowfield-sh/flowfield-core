import { reportError } from "./requestFeedback";
import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ContentStack, DetailHeading, DetailSection } from "./DetailLayout";
import { request, type Project } from "./workspace";

export function SetupInstructions({
  added,
  workspaceId,
  onDirty,
}: {
  added: (project: Project) => void;
  workspaceId: string;
  onDirty: (dirty: boolean) => void;
}) {
  const [path, setPath] = useState("");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [prefix, setPrefix] = useState("");
  const [busy, setBusy] = useState<"choosing" | "adding" | null>(null);
  useEffect(() => {
    onDirty(!!path);
  }, [path, onDirty]);
  return (
    <section className="setup-page" aria-label="Project setup instructions">
      <ContentStack space="section" className="pane-form">
        <DetailHeading title="Add a project" titleAs="h2" />
        <p>
          Choose an existing project for this workspace. Discuss its existing
          instructions with the Coordinator to decide what belongs in workspace
          guidance and what should stay in the repository.
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!path || !id.trim() || !name.trim() || !prefix.trim() || busy)
              return;
            setBusy("adding");
            try {
              const project = await request<Project>(
                `workspaces/${workspaceId}/projects`,
                "POST",
                {
                  path,
                  id: id.trim(),
                  name: name.trim(),
                  task_prefix: prefix.trim(),
                },
                undefined,
                180000,
              );
              onDirty(false);
              added(project);
            } catch (failure) {
              reportError(failure, "Could not finish adding project");
            } finally {
              setBusy(null);
            }
          }}
        >
          <fieldset
            disabled={busy !== null}
            className="content-stack"
            data-space="section"
          >
            <ContentStack>
              {path && (
                <Label className="field block">
                  Project directory
                  <Input aria-label="Project directory" value={path} readOnly />
                </Label>
              )}
              <div className="actions justify-end">
                <Button
                  type="button"
                  variant={path ? "outline" : "default"}
                  onClick={async () => {
                    setBusy("choosing");
                    try {
                      const selection = await request<{
                        path: string | null;
                      }>(
                        "projects/select-directory",
                        "POST",
                        undefined,
                        undefined,
                        310000,
                      );
                      if (selection.path) {
                        const defaults = await request<
                          components["schemas"]["ProjectSetupDefaults"]
                        >("projects/setup-defaults", "POST", {
                          path: selection.path,
                        });
                        setPath(selection.path);
                        setId(defaults.id);
                        setName(defaults.name);
                        setPrefix(defaults.task_prefix);
                      }
                    } catch (failure) {
                      reportError(
                        failure,
                        "Could not choose project directory",
                      );
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  <FolderOpen />
                  {busy === "choosing"
                    ? "Choosing directory…"
                    : path
                      ? "Change directory"
                      : "Choose directory"}
                </Button>
              </div>
            </ContentStack>
            {path && (
              <DetailSection title="Project details">
                <ContentStack space="section">
                  <Label className="field block">
                    Name
                    <Input
                      required
                      pattern={".*\\S.*"}
                      value={name}
                      maxLength={200}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </Label>
                  <ContentStack space="flush">
                    <Label className="field block">
                      Project ID
                      <Input
                        required
                        aria-describedby="project-id-help"
                        value={id}
                        maxLength={64}
                        pattern={"[a-z0-9][a-z0-9_\\-]{0,63}"}
                        onChange={(event) => setId(event.target.value)}
                      />
                    </Label>
                    <p id="project-id-help" className="detail-metadata">
                      A unique ID used in project links.
                    </p>
                  </ContentStack>
                  <ContentStack space="flush">
                    <Label className="field block">
                      Task prefix
                      <Input
                        required
                        aria-describedby="project-prefix-help"
                        value={prefix}
                        minLength={3}
                        maxLength={3}
                        pattern="[A-Za-z]{3}"
                        onChange={(event) =>
                          setPrefix(event.target.value.toUpperCase())
                        }
                      />
                    </Label>
                    <p id="project-prefix-help" className="detail-metadata">
                      Three unique letters for task IDs, such as FOL-1.
                    </p>
                  </ContentStack>
                </ContentStack>
              </DetailSection>
            )}
            {path && (
              <div className="actions justify-end">
                <Button type="submit">
                  {busy === "adding" ? "Adding project…" : "Add project"}
                </Button>
              </div>
            )}
          </fieldset>
        </form>
      </ContentStack>
    </section>
  );
}
