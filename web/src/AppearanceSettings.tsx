import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { ChoiceSelect } from "@/components/ui/choice-select";

type Theme = "system" | "light" | "dark";
const key = "flowfield.theme";
const options = { system: "System", light: "Light", dark: "Dark" } as const;
function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(key);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}
function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
// Apply before React mounts; preferences are presentation-only, not project state.
applyTheme(readTheme());

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    applyTheme(theme);
    const query = matchMedia("(prefers-color-scheme: dark)");
    const changed = () => applyTheme(theme);
    const stored = (event: StorageEvent) => {
      if (event.key === key || event.key === null) setTheme(readTheme());
    };
    query.addEventListener("change", changed);
    window.addEventListener("storage", stored);
    return () => {
      query.removeEventListener("change", changed);
      window.removeEventListener("storage", stored);
    };
  }, [theme]);
  return {
    theme,
    change: (next: string) => {
      const value = next as Theme;
      setTheme(value);
      try {
        localStorage.setItem(key, value);
      } catch {
        /* Apply for this window. */
      }
    },
  };
}

export function AppearanceSettings({
  appearance,
}: {
  appearance: ReturnType<typeof useTheme>;
}) {
  return (
    <Label className="field block">
      Theme
      <ChoiceSelect
        label="Theme"
        value={appearance.theme}
        onChange={appearance.change}
        options={Object.entries(options).map(([value, label]) => ({
          value,
          label,
        }))}
      />
    </Label>
  );
}
