import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      /**
       * Catches reading a `const` before its declaration -- a temporal dead
       * zone crash at runtime that `tsc` structurally CANNOT catch when the
       * read sits inside a closure, because TypeScript has no way to know
       * when a closure runs.
       *
       * Added after exactly that shipped: a `docs.filter((d) => …query…)`
       * placed above its own `useState` type-checked clean, built clean, and
       * threw `Cannot access 'query' before initialization` on first render.
       *
       * `functions: false` because function declarations hoist and calling
       * one before its definition is both legal and idiomatic here.
       */
      "@typescript-eslint/no-use-before-define": [
        "error",
        { variables: true, functions: false, classes: true, typedefs: false, enums: true },
      ],
    },
  },
];

export default eslintConfig;
