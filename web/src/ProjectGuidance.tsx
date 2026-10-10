import { toast } from "sonner";
import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";

type Guidance = components["schemas"]["GuidanceView"];

export function ProjectGuidance({
  projectId,
  active,
}: {
  projectId: string;
  active: boolean;
}) {
  const path = `projects/${projectId}/guidance`;
  const [refresh, setRefresh] = useState(0);
  const resource = useResource<Guidance>(active ? path : null, refresh);
  const [busy, setBusy] = useState(false);
  const value = resource.data;
  const nextSteps = value?.next_steps ?? [];

  async function install() {
    if (!value || resource.loading || busy) return;
    setBusy(true);
    resource.invalidate();
    try {
      const next = await request<Guidance>(path, "POST", {
        action: "install",
        expected_revision: value.revision,
      });
      resource.invalidate();
      resource.setData(next);
      toast.success(next.message || "Project guidance installed.");
    } catch (failure) {
      toast.error((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.info("Copied. Review before adding it to your project.");
    } catch {
      toast.error(
        "Could not copy automatically. Select and copy the preview text.",
      );
    }
  }

  return (
    <DetailSection title="Project guidance">
      <p>
        Install project instructions for standalone agents and worker
        preparation.
      </p>
      {resource.error && (
        <Alert variant="destructive">
          <AlertDescription>{resource.error}</AlertDescription>
        </Alert>
      )}
      {!value ? (
        <p>
          {resource.loading
            ? "Reading project guidance…"
            : "Project guidance is unavailable."}
        </p>
      ) : (
        <ContentStack space="section">
          <ContentStack>
            <p>
              {value.status === "installed" || value.status === "manual"
                ? "Project guidance is in place."
                : "Install the coordinator skill and its AGENTS.md reference. Existing instructions are preserved."}
            </p>
            {value.notices.map((notice) => (
              <p key={notice}>{notice}</p>
            ))}
            {!!nextSteps.length && (
              <ul>
                {nextSteps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
            )}
          </ContentStack>
          <div className="actions">
            <Button
              size="sm"
              disabled={busy || resource.loading || !value.can_install}
              onClick={() => void install()}
            >
              {value.status === "update_available"
                ? "Update project guidance"
                : "Install project guidance"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || resource.loading}
              onClick={() => {
                setRefresh((v) => v + 1);
              }}
            >
              {resource.loading ? "Refreshing guidance…" : "Refresh guidance"}
            </Button>
          </div>
          <Disclosure summary={<>Preview or copy guidance</>}>
            <ContentStack space="section">
              <DetailSection
                title="AGENTS.md section"
                actions={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void copy(value.section)}
                  >
                    Copy section
                  </Button>
                }
              >
                <pre className="evidence-output">{value.section}</pre>
              </DetailSection>
              <DetailSection
                title=".agents/skills/flowfield-coordinator/SKILL.md"
                actions={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void copy(value.skill)}
                  >
                    Copy skill
                  </Button>
                }
              >
                <pre className="evidence-output">{value.skill}</pre>
              </DetailSection>
            </ContentStack>
          </Disclosure>
        </ContentStack>
      )}
    </DetailSection>
  );
}
