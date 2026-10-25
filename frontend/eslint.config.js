import js from '@eslint/js';
import tsParser from '@typescript-eslint/parser';
import tsPlugin from '@typescript-eslint/eslint-plugin';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default [
  { ignores: ['dist', 'dist-e2e'] },
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2020,
      sourceType: 'module',
      globals: {
        window: 'readonly',
        document: 'readonly',
        HTMLElement: 'readonly',
        HTMLInputElement: 'readonly',
        fetch: 'readonly',
        File: 'readonly',
        FormData: 'readonly',
        crypto: 'readonly',
        localStorage: 'readonly',
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // TypeScript (via `tsc --noEmit`) already checks these accurately —
      // base ESLint doesn't see ambient/global .d.ts types (DOM globals,
      // the `React` namespace) or TS-only syntax (interface method params),
      // so it false-positives on both. Use the TS-aware unused-vars rule
      // instead of turning the check off entirely.
      'no-undef': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': 'warn',
    },
  },
  {
    // Playwright tests aren't React: its fixtures receive a function named
    // `use`, which the hooks rule would otherwise mistake for a React hook.
    files: ['e2e/**/*.ts', 'playwright.config.ts'],
    rules: {
      'react-hooks/rules-of-hooks': 'off',
    },
  },
];
