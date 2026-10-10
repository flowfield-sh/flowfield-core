import { toast } from "sonner";
import { useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { ProjectGuidance } from "./ProjectGuidance";
import { ProjectAgentChoice } from "./ProjectAgentChoice";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { request, type Project } from "./workspace";

export function SetupInstructions({
  added,
}: {
  added: (project: Project) => void;
}) {
  const [preview, setPreview] = useState(false);
  const templates = useResource<{ section: string; skill: string }>(
    preview ? "guidance-template" : null,
    0,
  );
  const [installGuidance, setInstallGuidance] = useState(true);
  const [registered, setRegistered] = useState<Project | null>(null);
  const [path, setPath] = useState("");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [prefix, setPrefix] = useState("");
  const [coordinator, setCoordinator] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [worker, setWorker] = useState<
    components["schemas"]["AgentChoice-Output"] | null
  >(null);
  const [capacity, setCapacity] = useState(1);
  const [busy, setBusy] = useState<"choosing" | "adding" | null>(null);
  return (
    <section className="setup-page" aria-label="Project setup instructions">
      <ContentStack space="section" className="welcome">
        <h1>Set up your project</h1>
        <p>
          Choose an existing project to plan work in the Coordinator and follow
          it on the board.
        </p>
        {registered ? (
          <ContentStack space="section">
            <p>
              {busy
                ? "Installing project guidance…"
                : "Your project was added. Guidance installation needs attention; your existing files are preserved."}
            </p>
            {!busy && <ProjectGuidance projectId={registered.id} active />}
            <Button disabled={!!busy} onClick={() => added(registered)}>
              Continue to project
            </Button>
          </ContentStack>
        ) : (
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (!path || !id.trim() || !name.trim() || !prefix.trim() || busy)
                return;
              setBusy("adding");
              try {
                const project = await request<Project>(
                  "projects/initialize",
                  "POST",
                  {
                    path,
                    id: id.trim(),
                    name: name.trim(),
                    task_prefix: prefix.trim(),
                    coordinator,
                    worker,
                    max_parallel: capacity,
                  },
                  undefined,
                  180000,
                );
                setRegistered(project);
                if (installGuidance) {
                  const guidancePath = `projects/${project.id}/guidance`;
                  const guidance =
                    await request<components["schemas"]["GuidanceView"]>(
                      guidancePath,
                    );
                  await request(guidancePath, "POST", {
                    action: "install",
                    expected_revision: guidance.revision,
                  });
                }
                added(project);
              } catch (failure) {
                toast.error((failure as Error).message);
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
                    <Input
                      aria-label="Project directory"
                      value={path}
                      readOnly
                    />
                  </Label>
                )}
                <div className="actions">
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
                          if (selection.path !== path) {
                            setCoordinator(null);
                            setWorker(null);
                            setCapacity(1);
                          }
                        }
                      } catch (failure) {
                        toast.error((failure as Error).message);
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
                <DetailSection title="Agents">
                  <p className="detail-metadata">
                    Optional. Change these later in Project settings.
                  </p>
                  <ProjectAgentChoice
                    key={`${path}:coordinator`}
                    path={path}
                    role="Coordinator"
                    value={coordinator}
                    capacity={1}
                    change={setCoordinator}
                    disabled={!!busy}
                  />
                  <ProjectAgentChoice
                    key={`${path}:worker`}
                    path={path}
                    role="Workers"
                    value={worker}
                    capacity={capacity}
                    change={(choice, cap) => {
                      setWorker(choice);
                      setCapacity(cap);
                    }}
                    disabled={!!busy}
                  />
                </DetailSection>
              )}
              {path && (
                <ContentStack>
                  <Label>
                    <input
                      type="checkbox"
                      checked={installGuidance}
                      onChange={(event) =>
                        setInstallGuidance(event.target.checked)
                      }
                    />{" "}
                    Install project guidance
                  </Label>
                  <ContentStack space="flush">
                    <p className="detail-metadata">
                      Helps standalone agents coordinate work and prepares
                      workers. Existing instructions are preserved.
                    </p>
                    {!installGuidance && (
                      <p className="detail-metadata">
                        Install later in Project settings → Coordinator. The
                        built-in Coordinator can already help you set up
                        workers.
                      </p>
                    )}
                  </ContentStack>
                  <Disclosure
                    summary="Preview project guidance"
                    onToggle={(event) => setPreview(event.currentTarget.open)}
                  >
                    <ContentStack space="section">
                      {templates.error && <p role="alert">{templates.error}</p>}
                      {templates.loading && <p>Loading guidance…</p>}
                      {templates.data && (
                        <ContentStack space="section">
                          <DetailSection title="AGENTS.md section">
                            <pre className="evidence-output">
                              {templates.data.section}
                            </pre>
                          </DetailSection>
                          <DetailSection title=".agents/skills/flowfield-coordinator/SKILL.md">
                            <pre className="evidence-output">
                              {templates.data.skill}
                            </pre>
                          </DetailSection>
                        </ContentStack>
                      )}
                    </ContentStack>
                  </Disclosure>
                </ContentStack>
              )}
              {path && (
                <div className="actions">
                  <Button type="submit">
                    {busy === "adding" ? "Adding project…" : "Add project"}
                  </Button>
                </div>
              )}
            </fieldset>
          </form>
        )}
      </ContentStack>
    </section>
  );
}
