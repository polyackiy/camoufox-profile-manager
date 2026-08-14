// eslint-config-next 16 ships real flat configs, so the FlatCompat shim that
// wrapped the old .eslintrc-style presets is gone along with @eslint/eslintrc.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  // Nothing generated: `.next` and `out` are build output, `next-env.d.ts` is
  // written by Next on every build. (`node_modules` is ignored by default.)
  { ignores: [".next/**", "out/**", "next-env.d.ts"] },
  ...nextCoreWebVitals,
  ...nextTypeScript,
];

export default config;
