import { ChoiceSelect } from "@/components/ui/choice-select";
import claude from "./assets/claude.svg";
import codex from "./assets/openai.svg";
import type { HarnessKind } from "./HarnessModels";

export function HarnessLogo({ kind }: { kind: HarnessKind }) {
  return (
    <img
      className="harness-logo"
      src={kind === "codex" ? codex : claude}
      alt=""
      aria-hidden="true"
    />
  );
}

export function HarnessSelect({
  value,
  disabled,
  compact,
  options,
  onChange,
}: {
  value: string;
  disabled: boolean;
  compact: boolean;
  options: { value: HarnessKind; name: string; disabled: boolean }[];
  onChange: (value: HarnessKind) => void;
}) {
  return (
    <ChoiceSelect
      value={value}
      label="Harness"
      placeholder="Choose a harness"
      compact={compact}
      onChange={(value) => onChange(value as HarnessKind)}
      disabled={disabled}
      options={options.map((option) => ({
        ...option,
        label: (
          <span className="harness-name">
            <HarnessLogo kind={option.value} />
            {option.name}
            {option.disabled ? " (setup needed)" : ""}
          </span>
        ),
      }))}
    />
  );
}
