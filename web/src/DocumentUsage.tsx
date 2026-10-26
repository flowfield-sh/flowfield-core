import { WorkspaceLink } from "./WorkspaceLink";
import { NativeSelect } from "@/components/ui/native-select";
import type { components } from "./api-schema";
import { ContentStack, DetailSection } from "./DetailLayout";
import { SettingsCheckbox } from "./SettingsCheckbox";
import { ResourceRetry } from "./ResourceRetry";
import { useResource } from "./useResource";

type Usage = components["schemas"]["DocumentUsageView"];
type Purpose = NonNullable<Usage["projectless"]>;
export const usageFields = (record?: Usage) => ({
  projectless: record?.projectless ?? null,
  projects: record?.projects.map(({ id, purpose }) => ({ id, purpose })) ?? [],
});

export function DocumentUsage({
  path,
  refresh,
}: {
  path: string;
  refresh: unknown;
}) {
  const resource = useResource<Usage>(path, refresh);
  const usage = resource.data;
  return (
    <ContentStack>
      <ResourceRetry resources={[resource]} />
      {usage && (
        <ContentStack space="tight">
          <p className="document-projects">
            Projects:{" "}
            {usage.projects
              .filter((p) => p.purpose)
              .map((p, i) => (
                <span key={p.id}>
                  {i > 0 && ", "}
                  <WorkspaceLink to={`/projects/${p.id}/overview`}>
                    {p.name}
                  </WorkspaceLink>
                </span>
              ))}
            {!usage.projects.some((p) => p.purpose) && "None"}
          </p>
          <p className="document-projects">
            {usage.projectless === "required"
              ? "Required reading for tasks without a project."
              : usage.projectless === "reference"
                ? "Reference for tasks without a project."
                : "Not included in tasks without a project."}
          </p>
        </ContentStack>
      )}
    </ContentStack>
  );
}

export function DocumentUsageFields({
  usage,
  value,
  change,
  guidance,
}: {
  usage: Usage;
  value: ReturnType<typeof usageFields>;
  change: (value: ReturnType<typeof usageFields>) => void;
  guidance: boolean;
}) {
  return (
    <DetailSection settings title="Use this document">
      {guidance ? (
        <p>Required for all tasks.</p>
      ) : (
        <ContentStack>
          <UsageChoice
            label="Include for tasks without a project"
            purposeLabel="Use for tasks without a project as"
            value={value.projectless}
            change={(projectless) => change({ ...value, projectless })}
          />
          {usage.projects.map((project) => (
            <UsageChoice
              key={project.id}
              label={project.name}
              purposeLabel={`Use for ${project.name} as`}
              value={
                value.projects.find((item) => item.id === project.id)
                  ?.purpose ?? null
              }
              change={(purpose) =>
                change({
                  ...value,
                  projects: value.projects.map((item) =>
                    item.id === project.id ? { ...item, purpose } : item,
                  ),
                })
              }
            />
          ))}
        </ContentStack>
      )}
    </DetailSection>
  );
}

function UsageChoice({
  label,
  purposeLabel,
  value,
  change,
}: {
  label: string;
  purposeLabel: string;
  value: Purpose | null;
  change: (value: Purpose | null) => void;
}) {
  return (
    <ContentStack space="tight">
      <SettingsCheckbox
        checked={value !== null}
        onChange={(event) => change(event.target.checked ? "required" : null)}
      >
        {label}
      </SettingsCheckbox>
      {value && (
        <label className="settings-checkbox-content flex flex-wrap items-center gap-2">
          <span>Use as</span>
          <NativeSelect
            size="sm"
            aria-label={purposeLabel}
            value={value}
            onChange={(event) => change(event.target.value as Purpose)}
          >
            <option value="required">Required source</option>
            <option value="reference">Reference material</option>
          </NativeSelect>
        </label>
      )}
    </ContentStack>
  );
}
