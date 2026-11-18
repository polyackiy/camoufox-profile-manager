# Interactive synchronizer capture gate

> **For agentic workers:** Use superpowers:executing-plans to implement the accepted #117 architecture in reviewable blocks.

**Goal:** Establish and test the trusted capture boundary before enabling interactive replay for user profiles.

**Architecture:** A packaged Firefox extension captures bounded pointer/scroll events in its isolated content-script world. A separate native-message host validates framing and launch-scoped channel data; the feasibility harness binds this host to a disposable leader and relays accepted input to one explicitly mapped disposable follower. No normal launch, web endpoint, settings flag or page-callable Playwright binding enables capture.

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
