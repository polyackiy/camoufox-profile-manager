# Desktop browser interface without a Camoufox fork

The manager's window and each profile's browser window are separate interfaces.
The manager uses pywebview for profiles, setup and recovery. Actual websites still
run inside the upstream Camoufox binary and its persistent Firefox profile.

## What is implemented

Profile → Advanced → Browser interface offers **Desktop** (the default) and
**Camoufox** (the upstream minimal theme). Close and reopen the profile to apply.
The desktop choice passes upstream config `disableTheming: true` and
`showcursor: false`. It restores ordinary browser chrome and removes the
automation cursor overlay. Switching back passes `disableTheming: false`.
Existing profiles without the new setting use the desktop choice too.

This is a supported Camoufox switch, introduced by its maintainer in
[upstream issue #179](https://github.com/daijro/camoufox/issues/179). The current
[property manifest](https://github.com/daijro/camoufox/blob/main/settings/properties.json)
and installed 152.0.4-beta.29 both contain these keys. Camoufox accepts config
overrides through its [Python API](https://camoufox.com/python/usage/).

No installed browser files, bundled CSS, engine patches or fingerprint properties
are rewritten. Interface keys are not part of the stored hardware fingerprint;
the existing pin and profile directory remain in use. A real-browser regression
test switches modes and checks hardware observations, a retained site cookie and
localStorage. It passed on 152.0.4-beta.29 and on a fresh installation of
156.0.1-beta.36 (2026-10-07). The latter also completed the first-run binary,
GeoIP and add-on download into an empty, isolated cache.

On macOS with Camoufox 152.0.4-beta.29, the native window was inspected: close
buttons are visible, the New Tab control adds a tab in the same window, and
Command-W closes that tab while leaving the other one open. This does not claim
that every Firefox feature disabled by Camoufox has been restored. Playwright's
`new_page()` can still produce another window; manual New Tab is a separate path.

## Maintenance policy

1. Prefer upstream config switches and narrowly scoped browser UI preferences.
   Keep privacy, network and fingerprint defaults out of appearance settings.
2. Before changing the supported Camoufox version, run the real-browser suite,
   inspect native tab creation/closing on the supported desktop OSes, and verify
   that the property manifest still accepts the two keys.
3. If a control cannot be restored through supported settings, propose a small
   upstream toggle with a reproduction. A new engine fork is disproportionate
   to a browser-chrome problem.
4. Extensions can add bookmarks or tab-management tools later; they are not a
   replacement for native window controls. Avoid a large userChrome.css skin:
   Firefox's internal UI selectors are not a stable integration contract.
5. Do not embed web pages in an Electron/WebView replacement or implement a
   screenshot-stream browser shell. Those approaches either replace Camoufox's
   engine or recreate focus, downloads, permissions, accessibility and tab state.

Next candidates are optional bookmark-toolbar visibility and explicit startup
page/session preferences. They should stay independent of hardware identity,
with clear user control and compatibility checks per upstream release.
