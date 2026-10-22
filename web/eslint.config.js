import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["test-results/**", "playwright-report/**", "src/api-schema.ts"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-alert": "error",
      "no-restricted-globals": ["error", "alert", "confirm", "prompt"],
      "no-restricted-properties": [
        "error",
        ...["alert", "confirm", "prompt"].map((property) => ({
          property,
          message: "Use shared rendered feedback or an accessible app dialog.",
        })),
      ],
    },
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": hooks },
    rules: hooks.configs.recommended.rules,
  },
);
