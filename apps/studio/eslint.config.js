import js from "@eslint/js";
import eslintConfigPrettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist/",
      "dist-mock/",
      "playwright-report/",
      "test-results/",
      "src/api/schema.d.ts",
    ],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
  {
    // Only src/api/ talks to the server: components use its typed functions, so
    // tokens, timeouts and error shapes are handled in one place.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/**", "src/**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "no-restricted-globals": [
        "error",
        {
          name: "fetch",
          message: "Call the typed functions in src/api/ instead of fetch.",
        },
      ],
    },
  },
  eslintConfigPrettier,
);
