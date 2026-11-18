# Interactive capture feasibility

This is the first implementation block of [#117](https://github.com/polyackiy/camoufox-profile-manager/issues/117)
and its [architecture](interactive-synchronizer-design.md). It is a developer test,
not an available synchronizer. There is no enable switch, application endpoint,
normal-launch integration or automatic capture of existing user profiles.

## What the gate checks

Two disposable local browser profiles open a controlled local page. An
origin-limited Firefox WebExtension in the leader captures trusted top-frame
single clicks and bounded wheel input. Its background sends messages through
Firefox Native Messaging to a separate console host. The probe authenticates a
launch-scoped key and challenge, then checks the actual native-host ancestry,
browser executable, profile path and browser process birth time. An extension ID
or copied key alone cannot enroll another profile.

The protocol binds one generation, tab, exact origin and 900 × 700 viewport. It
rejects ambiguous JSON, unknown fields, oversized/truncated frames, invalid
geometry, wrong proofs, expired generations and out-of-order messages. Input
stays in a bounded memory queue; disconnect ends the generation without retry or
reconnection. The test maps its known follower explicitly, checks click delivery
and a controlled wheel example, and invalidates the channel on navigation.

No text, keys, paste, clipboard, selectors, DOM snapshots, frames or form values
are transmitted. Synthetic page events and editable controls, labels for controls
and shadow hosts (including closed roots on ordinary elements) are excluded.
`isTrusted` does not prove physical human input: these tests also use upstream
browser automation, which can create trusted events.

The per-launch key is passed in the process environment, outside add-on files and
browser profile storage. Closed disposable profiles are checked for the original
credential. This trusts the local OS account and installed extensions; it does
not promise protection from another process with that account's privileges.

## Run locally

Use the repository's locked Python environment and installed paired browser;
the probe never downloads or selects another browser. Install the ordinary
development/build dependencies and prepare the paired browser using the same
documented development prerequisites as the browser compatibility suite.

```sh
uv sync --locked --extra dev --extra build --extra sync
uv run --no-sync pytest tests/unit/test_action_capture.py tests/unit/test_action_capture_host.py tests/unit/test_action_capture_extension.py -q
CPM_ACTION_CAPTURE_ACCEPTANCE=1 uv run --no-sync pytest tests/browser/test_action_capture.py -q -m browser --no-network
```

Node is needed for the extension contract test. On Linux, run browser tests under
`xvfb-run -a`. The source console host is supported on macOS/Linux; Windows uses
the frozen console executable:

```sh
uv run --no-sync pyinstaller --noconfirm --clean --onedir --console --paths src --name cpm-capture-host scripts/action_capture_probe_entry.py
```

Set `CPM_CAPTURE_FROZEN_HOST` to the absolute generated executable path (with
`.exe` on Windows) and run the same explicitly enabled browser tests. In
PowerShell, set environment variables with `$env:NAME = 'value'` before running
the commands. Ordinary test runs skip these two native tests.

During each test, one random native-host name is registered in Firefox's real
per-user manifest directory on macOS/Linux, or a new HKCU native-host key on
Windows. An existing manifest/key is never replaced. Cleanup removes only the
unchanged registration owned by that test and its disposable files. Firefox on
macOS does not reliably redirect native-host discovery with a temporary `HOME`;
using only a pretend browser home is insufficient. Do not run the gate against
personal browser profiles or interrupt it without allowing teardown.

The dedicated **Interactive capture feasibility** workflow runs the locked
browser on Linux, Windows, macOS ARM and macOS Intel, with a frozen helper on all
four and source controls on POSIX. General CI does not enable capture. A helper
executable is not the installed manager; these results cannot establish installer
registration, lifecycle or updater acceptance.

## Boundaries still blocking a user preview

- [#162](https://github.com/polyackiy/camoufox-profile-manager/issues/162): manager-owned admission, administrator cookie authority, exact live session/tab mappings, private IPC access restrictions, installed-host discovery, ordered playback, bounded I/O and stop/drain integration with workflows, updates and shutdown.
- Wheel units must remain explicit. Firefox can report lines or pixels, and
  upstream Camoufox turns an automated wheel command into native notches. The
  test's known single-notch fixture is not a general conversion for arbitrary
  manual wheels, trackpads, nested scrollers or different site layouts. A
  unit-aware playback adapter and actual manual-device acceptance are required
  before enabling scrolling in the manager. See [MDN's unit contract](https://developer.mozilla.org/en-US/docs/Web/API/WheelEvent/deltaMode)
  and the [upstream input implementation](https://github.com/daijro/camoufox/blob/main/browser/additions/juggler/protocol/PageHandler.js).
- [#163](https://github.com/polyackiy/camoufox-profile-manager/issues/163): explicit paused controller, reviewed origins and compatible viewports, visible selected targets, tab mapping, EN/RU/zh-CN controls and native/security acceptance.
- No arbitrary attachment to running profiles, physical-device parity, browser
  chrome, text/IME, keyboard or cloud relay is covered. Browser binaries,
  fingerprints and proxy settings remain owned by upstream/current manager
  behavior. No undetectability claim is made.

Keep these restrictions explicit in issue and release descriptions. Passing the
capture gate does not close #117 or constitute a security audit of a complete
synchronizer.
