# Interactive profile action synchronization — proposed architecture

Status: design only, not a shipped feature. This follows the completed declarative
[workflows](automation.md) in [#117](https://github.com/polyackiy/camoufox-profile-manager/issues/117).
It is separate from experimental encrypted device snapshot synchronization.
No capture agent, endpoint, browser permission or automatic start is introduced
by this document. Native feasibility and security evidence are required before
implementation is enabled.

## Product scope

One explicitly chosen local leader and a reviewed list of local followers form a
short-lived session. Profile selection never means all running browsers. The
first supported cohort should be deliberately small (two to four profiles in
all), with a single controller and administrator cookie authority. Existing
operators/grants can be considered separately after the admission model is
validated. API keys, a web page, relay membership and an extension ID alone do not
grant control authority.

The useful commercial ideas are an explicit leader, visible target selection,
stop/pause controls and clear tab/window compatibility. Dolphin documents those
flows and tab/window-size limitations; AdsPower documents target selection and a
separate synchronizer console. Their engine/platform support does not prove
Firefox/Camoufox compatibility. These are workflow references, not a security or
fingerprint guarantee:

- [Dolphin synchronizer](https://docs.dolphin-anty.com/en/working-with-profiles/profile-synchronizer-in-dolphin-anty).
- [AdsPower synchronizer](https://help.adspower.com/docs/synchronizer).

The first feasibility trials use disposable local-only profiles; compatibility
with device-synchronized profiles requires its own closed-profile/export proof.
The initial capture prototype should cover trusted pointer clicks, scrolling
and explicit tab mapping on controlled test pages. Text/IME, passwords, paste,
clipboard, file dialogs, downloads, browser chrome and arbitrary keyboard
shortcuts are outside that initial acceptance scope. Do not describe this limited
prototype as a complete synchronizer. Sensitive form values must never be
captured, forwarded or retained. Followers can already contain authenticated site
sessions: even a click may have lasting effects, and accepted actions cannot be
rolled back as a transaction.

## Maintain upstream browser ownership

Keep using the upstream Camoufox binary and supported add-on/Playwright APIs.
Do not fork Firefox, modify installed browser files or CSS, install a global
keyboard/mouse hook, expose a debug server, or change proxy/fingerprint settings.
Window tiling is a separate optional capability; it must not silently resize a
profile's declared screen or promise native window positioning through viewport
APIs. Unequal viewport geometry blocks coordinate replay until explicitly
resolved; do not silently scale coordinates onto a different layout.

Do not promise attachment to an arbitrary already-running browser. The supported
Camoufox add-on loader operates at launch; until a supported live-attachment path
is proven, joining requires an explicitly prepared launch and review of any
needed restart. Never close a working browser implicitly.

A privileged, bundled Firefox WebExtension is the preferred capture candidate,
subject to the feasibility gates below. Firefox content scripts have a separate
extension execution environment, and Native Messaging supports a restricted
extension-to-host channel. Neither mechanism is sufficient authentication by
itself. The prototype must establish the complete process/profile/session binding
and prevent ordinary web content from injecting capture messages.

[Playwright expose_binding](https://playwright.dev/python/docs/api/class-page#page-expose-binding)
makes a callable available to page JavaScript. A secret URL, randomized binding
name or a claimed `isTrusted` boolean in its payload cannot turn such a page-callable
bridge into trusted operator input. Do not use that bridge as control authority.
Likewise, [isTrusted](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
distinguishes some synthetic events but is not proof of a physical human action:
user-agent operations can generate trusted events. Test automation feedback and
script-induced navigation explicitly.

Primary integration references:
[Firefox content scripts](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Content_scripts),
[Native Messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging),
[existing managed extensions](extensions.md), and
[upstream browser interface policy](browser-interface.md).

## Boundaries and lifetime

The manager owns admission, authority, target identity and playback. A capture
adapter may only report a bounded event for the already accepted leader/tab;
it cannot choose a follower, provide JavaScript/selectors to evaluate, launch a
browser, enroll synchronization, read files or change configuration.

A run-scoped capability must be bound to the exact accepted browser session,
profile, extension channel and generation. Keep it out of page-accessible globals,
URLs, logs, API responses, browser storage and synchronized profile metadata.
A native-host allowlist/extension ID alone does not prove which profile is sending.
The spike must demonstrate a practical credential bootstrap and stale-session
rejection on all four desktop targets; this is an unresolved implementation gate,
not a guarantee already supplied by the platform. Treat the local OS account and
explicitly installed third-party extensions as existing trust boundaries.

Use bounded private IPC, no public listener or web-callable action receiver. Any
required per-user native-host registration must be explicit, versioned and removed
without touching another installation's manifest. Source and installed modes
need distinct executable discovery; HTTP input cannot supply executable paths.
Do not silently add broad permissions to every profile. Loading the adapter must
be explicit, separate from user-assigned library extensions, and its capture capability invalidated on
Stop, process exit or restart. Detach capture listeners on Stop; do not promise
that Firefox unloads the add-on without closing the browser. Prepared launches
never imply automatic session start. A closed-profile backup/snapshot must contain no
usable active capability, run journal or captured event. Prove this in tests;
do not assume that a temporary source add-on leaves no Firefox cache behind.

## State, ordering and failure

Use `idle → preparing → paused → running → draining → stopped`. Preparation checks
live administrator authority, exact browser/profile identities, existing leases,
maintenance and workflow admission, target tabs, allowed origins and viewport
compatibility. No actions run before an explicit start. Membership/leader changes
require draining and a new reviewed generation. Only one cohort may control a
profile; normal Close and authority revocation remain available.

Each event has a generation, monotonically increasing sequence, mapped tab and
bounded payload. Reject extras, stale generations, unrecognized tabs/origins and
out-of-range coordinates. Verify authority and session/admission immediately before
playback, not only when the controller opens. Keep one bounded ordered queue per
follower with acknowledgement and a short deadline. A queue overflow or unknown
result pauses the cohort and discards undispatched events; never replay a click
automatically after disconnect, timeout, restart or reauthorization. Acknowledged
input dispatch is not proof that the website completed the intended business action.

Only the leader produces events. Follower playback cannot become a new source.
Tab mapping uses explicit handles within the accepted session, not matching tab
indices or page titles. A popup, unmapped/new tab, closed tab, navigation outside
the selected origins, geometry change, modal dialog, target loss, authority change
or update maintenance pauses/stops the cohort after draining already accepted
playback. Revocation/controller loss stops it instead of leaving a resumable
authorized session. Cross-origin frames and privileged
browser pages are unsupported initially. Do not equate a navigation event with
user intent or blindly broadcast redirect/query-bearing URLs to other accounts.

Stop rejects new events immediately, cancels waiting events and drains accepted
playback before releasing control admission. Repeated cancellation cannot release
admission while a worker still dispatches. Disconnect/logout/process restart never
resumes a cohort. Update/checkpoint and shutdown use the same stop/drain path.
An unresponsive browser must fail closed, with a bounded recovery path that does
not falsely mark an unconfirmed input as cancelled.

## Records and acceptance

Store only allowlisted lifecycle/activity metadata: actor, reviewed profile IDs,
start/pause/stop and fixed outcome categories under the existing activity policy.
Do not persist capture payloads, text, coordinates, page URLs/titles, selectors,
clipboard data, site cookies, IPC capabilities, screenshots or DOM snapshots.
No cloud relay is involved. Support reports follow the
[closed diagnostic contract](operation-diagnostics.md).

Before enabling even an experimental preview:

1. Prove trusted-channel/profile binding and capability bootstrap on Windows,
   Linux and macOS ARM/Intel with installed packages and source-mode controls.
2. Demonstrate that a hostile page/frame, forged event, stale/replayed message,
   copied extension ID and reconnect cannot dispatch follower actions. Cover
   follower feedback, unsupported/sensitive input and authority revocation.
3. Exercise ordering, overflow, slow/hung follower, partial completion, tab/origin
   changes, controller loss, repeated cancellation and concurrent workflow/update
   admission. Stop must drain without an automatic replay.
4. Verify unchanged proxy routing, pinned fingerprint and normal tabs; record
   page-observable adapter effects without claiming undetectability. Preserve
   cookies/data through restart. Show that closed-profile backups/exports/device
   snapshots contain no active control capability or captured event data.
5. Record native package identities, compatibility limits and reproducible tests.
   Verify English/Russian/Simplified Chinese controls and accessibility before a
   user-facing implementation ships. Do not infer physical/enterprise coverage
   from same-runner tests or conflate this feature with device-sync production sign-off.

Implement in separately reviewable blocks: capture/channel feasibility; broker and
admission; explicit controller and tab mapping; native/security acceptance. If the
privileged channel cannot satisfy the first two gates without a browser fork,
keep capture disabled and revise this design rather than substituting an
untrusted page bridge.
