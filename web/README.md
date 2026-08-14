# Web UI

The Camoufox Profile Manager front end: a Next.js 16 app that FastAPI serves as a
static export, so the whole product runs as one process on one port.

## Running it

Most of the time you do not need this directory at all — `camoufox-pm` serves a
prebuilt UI. Work here only when changing the interface.

```bash
npm install
npm run dev        # http://localhost:3000, proxies /api to http://localhost:8000
```

Point the dev proxy somewhere else with `API_PROXY_TARGET`:

```bash
API_PROXY_TARGET=http://localhost:8099 npm run dev
```

To produce the bundle the Python package ships:

```bash
python ../scripts/build_webui.py
```

That runs the static export and copies `out/` into `src/camoufox_pm/webui/`.

## Structure

```
src/
  app/
    page.tsx           Profiles — the main registry
    groups/page.tsx    Groups
    settings/page.tsx  Effective configuration and usage
    layout.tsx         Fonts, shell and toasts
    globals.css        Design tokens and component classes
  components/
    app-shell.tsx      Fixed rail + work area
    profile-form.tsx   Create and edit a profile (one form, both modes)
    modal.tsx          Dialog shell and confirm dialog
    toast.tsx          Non-blocking notifications
    empty-state.tsx    First-run and no-results states
  lib/api.ts           Typed API client
```

## Design

The UI is an instrument panel: a dense control surface where colour carries
meaning instead of decoration.

- **One signal colour.** Orange marks the primary action and a running browser —
  nothing else. A running profile is the only thing on screen that draws the eye,
  so the table reads as a status board.
- **Hairlines, not boxes.** Rows and sections are separated by 1px rules rather
  than nested cards.
- **Monospace for machine values.** IDs, proxies and coordinates are mono so they
  align and stay scannable; prose is IBM Plex Sans.
- **Restraint in motion.** Rows stagger in once on load and the running dot
  pulses. Everything respects `prefers-reduced-motion`.

Tokens live in `@theme` in `globals.css`; there is no Tailwind config file
(Tailwind v4 reads the CSS). Fonts are pulled in by `next/font`, which self-hosts
them at build time so the desktop bundle works offline.
