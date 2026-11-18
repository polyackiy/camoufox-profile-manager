# Interactive synchronizer capture gate

> **For agentic workers:** Use superpowers:executing-plans to implement the accepted #117 architecture in reviewable blocks.

**Goal:** Establish and test the trusted capture boundary before enabling interactive replay for user profiles.

**Architecture:** A packaged Firefox extension captures bounded pointer/scroll events in its isolated content-script world. A separate native-message host validates framing and launch-scoped channel data; the feasibility harness binds this host to a disposable leader and relays accepted clicks to one explicitly mapped disposable follower and verifies unit-tagged native wheel capture. No normal launch, web endpoint, settings flag or page-callable Playwright binding enables capture.

**Tech stack:** Python, Firefox WebExtension MV2, upstream Camoufox/Playwright, native messaging, pytest and native Actions runners.

**Spec:** `docs/interactive-synchronizer-design.md`, #117 priority 1. This is its first implementation block, not the complete controller.

## Global constraints

- Two disposable profiles only; manual product admission/controller remain disabled.
- No browser fork, browser-file patches, global input hook or public action listener.
- No text, passwords, keyboard, paste, clipboard, frame input or URL replay.
- Capabilities exist only in process memory/environment and private ephemeral host configuration outside browser profiles. Native host registration must be owned and removed, preserving existing installations.
- A page-callable binding, extension ID or event.isTrusted payload alone cannot authorize playback.
- Reject unknown fields, oversized messages, invalid coordinates, origin/tab/geometry changes, replayed sequence and stale generation. Disconnect invalidates the channel; no replay.
- Record only pass/fail/count diagnostics, never event payloads, secret material, URLs, titles, DOM or profile data.

## Review focus

- A same-origin hostile page must not forge an event or supply its own command fields.
- A copied extension in a different browser profile must not authenticate as the accepted leader.
- Stop/reconnect must not turn an old event into a new action.
- Sensitive fields, same/cross-origin frames, synthetic events and unreviewed navigation must not enter the capture stream.
- Native host registration and temporary data must leave existing user resources untouched.

## Tasks

1. **Bounded protocol** (`core/action_capture.py`, `tests/unit/test_action_capture.py`): write failing tests for strict native framing, closed schemas, session authentication, ordered sequences, expiration and invalidation; implement the minimal independent boundary and pass the tests.
2. **Privileged capture probe** (`scripts/action_sync_probe.py`, `scripts/action_capture_extension/*`, `tests/unit/test_action_capture_extension.py`): generate an explicit origin-limited add-on and isolated native host registration; capture only trusted top-frame noneditable pointer/scroll input; keep the launch credential out of add-on files. Validate with JS hostile-page tests and two native browsers.
3. **Native gate** (`.github/workflows/action-capture.yml`, browser tests): exercise actual native messaging/profile binding on Linux, Windows and macOS ARM/Intel; use only disposable data. Keep failures visible and UI/controller disabled until gate evidence is complete.
4. **Review and documentation**: independent security review, relevant backend/type/lint and native tests, update #117 and focused GitHub tasks with exact evidence. Merge only completed scope; no installer release for this block.

## Follow-on blocks

The broker, admission and lifecycle must recheck administrator cookie authority, own the exact live sessions and drain playback before maintenance/lease release. The controller must explicitly review origins, matching viewports and mapped tab handles, start paused and localize EN/RU/zh-CN controls. These remain separate issues until the capture gate proves practical.

## Implementation evidence

- #161 tracks this block; PR #164 contains the source. #162/#163 retain the
  production broker/admission and controller/installed-manager acceptance scopes.
- Protocol, host and extension contracts: 42 tests pass, including explicit
  wheel units and unit-specific bounds. Ruff and core Mypy pass.
- Independent review found a closed-shadow sensitive-input gap and a broker
  cleanup gap on host-preparation failure. Both have regression coverage and
  fixes. The Firefox extension-only `Element.openOrClosedShadowRoot` getter is
  required; the Chromium `browser.dom` API is not interchangeable.
- Two real-browser cases pass locally in source and frozen-helper modes using
  the existing 0.5.8 / Firefox 156 pair as a control. The locked 0.5.9 / Firefox
  158 four-target native workflow is a separate required gate, not inferred from
  that local control. Full backend verification passes: 2526 passed, 19 skipped; resource and unraisable warnings are errors.
- Capture preserves wheel units. The native wheel-capture test does
  not implement a general manual-device playback adapter; this remains explicit in #162.
- All 14 native capture cases pass on the pinned SDK 0.5.9 / Firefox
  158.0.1-beta.41 pair across four targets, including source controls on POSIX
  and frozen helpers everywhere. The broader Windows suite exposed #165 in an
  unchanged extension-import test. A Windows 2025 baseline control reproduced
  its immediate missing marker and then observed the correct 2.0 marker. The
  fixture now waits for presence with a deadline, retaining the exact version
  assertions and closing the browser on failure. This is test readiness, not a
  claim that production archive/extension code was repaired.
- An Intel repeat reached the 180s outer deadline before a host connected.
  Fixed-phase/coroutine-only controls then passed both source/frozen modes:
  source cold launch took 126s, tab opening 33s, and the first complete case 174s.
  Intel's outer per-test budget is 300s with a 120s diagnostic watchdog. Native
  handshake/arming, generation expiry and all semantic assertions keep their own
  bounds. This addresses test bootstrap margin, not OS/SDK startup reliability.
- Full local typing with the build extra exposed a pre-existing recovery bridge
  handle inferred as `None` despite `create_window` returning `Window | None`.
  The handle now declares its optional type through a checking-only import.
  All 122 manager modules and the eight existing recovery bridge tests pass;
  the Linux native gate also checks types with the locked desktop build extras.
