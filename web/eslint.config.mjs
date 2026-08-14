// eslint-config-next 16 ships real flat configs, so the FlatCompat shim that
// wrapped the old .eslintrc-style presets is gone along with @eslint/eslintrc.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";
import { version as reactVersion } from "react";

const config = [
  // Nothing generated: `.next` and `out` are build output, `next-env.d.ts` is
  // written by Next on every build. (`node_modules` is ignored by default.)
  { ignores: [".next/**", "out/**", "next-env.d.ts"] },
  ...nextCoreWebVitals,
  ...nextTypeScript,
  // eslint-config-next asks eslint-plugin-react to detect the React version, and
  // detection calls the `context.getFilename()` that ESLint 10 removed — which
  // crashed the whole run, since the bundled plugin (7.37.5) has had no release
  // since April 2025. Reading the version from React itself is what detection
  // was going to arrive at anyway, and it never touches the removed API.
  { settings: { react: { version: reactVersion } } },
];

export default config;
