import { ChoiceSelect } from "@/components/ui/choice-select";
import claude from "./assets/claude.svg";
import codex from "./assets/openai.svg";
import pi from "./assets/pi.svg";
import type { HarnessKind } from "./HarnessModels";

const logos: Record<HarnessKind, string> = {
  codex,
  "claude-code": claude,
  pi,
};

export function HarnessLogo({ kind }: { kind: HarnessKind }) {
  return (
    <img className="harness-logo" src={logos[kind]} alt="" aria-hidden="true" />
  );
}

export function HarnessSelect({
  value,
  disabled,
  loading = false,
  compact,
  options,
  onChange,
}: {
  value: string;
  disabled: boolean;
  loading?: boolean;
  compact: boolean;
  options: { value: HarnessKind; name: string; disabled: boolean }[];
  onChange: (value: HarnessKind) => void;
}) {
  return (
    <ChoiceSelect
      value={loading ? "" : value}
      label="Harness"
      placeholder={loading ? "Loading harnesses…" : "Choose a harness"}
      compact={compact}
      onChange={(value) => onChange(value as HarnessKind)}
      disabled={disabled || loading}
      options={(loading ? [] : options).map((option) => ({
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
