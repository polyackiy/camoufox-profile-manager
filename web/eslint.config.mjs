// eslint-config-next 16 ships real flat configs, so the FlatCompat shim that
// wrapped the old .eslintrc-style presets is gone along with @eslint/eslintrc.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  // Not linted, and linting them is slow: `out` and `.next` are build output.
  { ignores: [".next/**", "out/**", "node_modules/**", "next-env.d.ts"] },
  ...nextCoreWebVitals,
  ...nextTypeScript,
];

export default config;
