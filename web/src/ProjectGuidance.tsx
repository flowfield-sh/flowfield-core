import { StatusNotice } from "./StatusNotice";
import { ResourceRetry } from "./ResourceRetry";
import { reportError } from "./requestFeedback";
import { toast } from "sonner";
import { useState } from "react";
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
      reportError(failure, "Could not install project guidance");
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
      {!value && <ResourceRetry resources={[resource]} />}
      {!value ? (
        <p>
          {resource.loading
            ? "Reading project guidance…"
            : "Project guidance is unavailable."}
        </p>
      ) : (
        <ContentStack space="section">
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
          <StatusNotice
            title={
              busy
                ? "Installing project guidance…"
                : {
                    installed: "Project guidance installed",
                    manual: "Project guidance supplied manually",
                    available: "Project guidance not installed",
                    update_available: "Project guidance update available",
                    partial: "Project guidance needs repair",
                    conflict: "Project guidance conflicts with existing files",
                  }[value.status]
            }
            tone={
              busy
                ? "progress"
                : value.status === "conflict" || value.status === "partial"
                  ? "attention"
                  : value.status === "installed" || value.status === "manual"
                    ? "success"
                    : "neutral"
            }
          >
            <p>
              {value.status === "installed" || value.status === "manual"
                ? "New coding conversations can use these project instructions."
                : "Install the coordinator skill and its AGENTS.md reference. Existing instructions are preserved."}
            </p>
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
          </StatusNotice>
          {!!value.notices.length && (
            <StatusNotice title="Project files need review" tone="attention">
              {value.notices.map((notice) => (
                <p key={notice}>{notice}</p>
              ))}
            </StatusNotice>
          )}
          {!!nextSteps.length && (
            <DetailSection title="Next steps">
              <ol className="list-decimal pl-5 space-y-2">
                {nextSteps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </DetailSection>
          )}
        </ContentStack>
      )}
    </DetailSection>
  );
}
