import type { ReactNode } from "react";
import { Select } from "radix-ui";
import { Check, ChevronDown } from "lucide-react";
import "./choice-select.css";

export function ChoiceSelect({
  value,
  label,
  placeholder,
  options,
  onChange,
  disabled = false,
  compact = false,
  required = false,
}: {
  value: string;
  label: string;
  placeholder?: string;
  options: { value: string; label: ReactNode; disabled?: boolean }[];
  onChange: (value: string) => void;
  disabled?: boolean;
  compact?: boolean;
  required?: boolean;
}) {
  return (
    <Select.Root
      value={value}
      onValueChange={(next) => {
        // Radix's form control can emit an empty value when options reload.
        // Only an actual offered choice changes the draft.
        if (
          next &&
          next !== value &&
          options.some((option) => option.value === next && !option.disabled)
        )
          onChange(next);
      }}
      disabled={disabled}
      required={required}
    >
      <Select.Trigger
        data-slot="choice-select"
        className="choice-select"
        data-compact={compact}
        data-value={value}
        aria-label={label}
      >
        <Select.Value placeholder={placeholder} />
        <Select.Icon>
          <ChevronDown size={14} />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          className="choice-select-menu"
          position="popper"
          sideOffset={4}
        >
          <Select.Viewport>
            {options.map((option) => (
              <Select.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="choice-select-option"
                data-value={option.value}
              >
                <Select.ItemText>{option.label}</Select.ItemText>
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
