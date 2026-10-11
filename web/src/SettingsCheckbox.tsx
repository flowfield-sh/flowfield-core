import { useId, type ComponentProps, type ReactNode } from "react";
import { ContentStack } from "./DetailLayout";

export function SettingsCheckbox({
  children,
  hint,
  ...props
}: Omit<ComponentProps<"input">, "type" | "children"> & {
  children: ReactNode;
  hint?: ReactNode;
}) {
  const id = useId();
  return (
    <ContentStack space="tight" className="settings-checkbox">
      <label>
        <input
          {...props}
          type="checkbox"
          aria-describedby={hint ? id : undefined}
        />
        <span>{children}</span>
      </label>
      {hint && (
        <p id={id} className="detail-metadata">
          {hint}
        </p>
      )}
    </ContentStack>
  );
}
