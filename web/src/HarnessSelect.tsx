import { Select } from "radix-ui";
import { Check, ChevronDown } from "lucide-react";
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
    <Select.Root
      value={value}
      onValueChange={(value) => onChange(value as HarnessKind)}
      disabled={disabled}
    >
      <Select.Trigger
        className="harness-select"
        data-compact={compact}
        aria-label="Harness"
      >
        <Select.Value placeholder="Choose a harness" />
        <Select.Icon>
          <ChevronDown size={14} />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          className="harness-select-menu"
          position="popper"
          sideOffset={4}
        >
          <Select.Viewport>
            {options.map((option) => (
              <Select.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="harness-select-option"
              >
                <Select.ItemText>
                  <span className="harness-name">
                    <HarnessLogo kind={option.value} />
                    {option.name}
                    {option.disabled ? " (setup needed)" : ""}
                  </span>
                </Select.ItemText>
                <Select.ItemIndicator>
                  <Check size={14} />
                </Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
