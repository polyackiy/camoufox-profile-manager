# Synchronization support and validation matrix

Synchronization is an experimental implementation. This matrix distinguishes
completed checks from environments and guarantees that remain unverified.
Passing cryptographic tests does not prove that every browser session migrates
correctly, and does not constitute an external human cryptographic audit.

## Recorded CI evidence

The [sync protocol run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38456215805)
for [PR #94](https://github.com/polyackiy/camoufox-profile-manager/pull/94), created
2026-10-13, completed on commit
`6533d2f510d759fca2e10c40523f44663a0ed78e`. These results apply to that commit;
later integration changes require another run.

| Platform | Protocol/state unit tests | Source and frozen crypto/recovery smoke | Actual OS vault smoke |
| --- | --- | --- | --- |
| Ubuntu (`ubuntu-latest`) | Passed | Passed | Not run: Secret Service needs a usable desktop session |
| Windows (`windows-latest`) | Passed | Passed | Passed: Windows Credential Manager |
| macOS Apple Silicon (`macos-15`) | Passed | Passed | Passed: Keychain |
| macOS Intel (`macos-15-intel`) | Passed | Passed | Passed: Keychain |

The separate CIRCL Go HPKE interoperability job also passed in that run. This is
independent implementation comparison, not independent human review of the whole
protocol. Native vault smoke tests create isolated test credentials and remove
them afterward; they do not establish behavior of every locked, enterprise-managed
or headless credential store.

## Integrated native crypto and vault evidence

The [integrated security matrix](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38471961634)
passes at `5bd37f6256599523de411e0ee6ffd4178ba7c143` on Ubuntu, Windows,
macOS Apple Silicon and macOS Intel. This run includes the full sync unit/negative
suite, source and frozen-codec/recovery checks, and the independent CIRCL HPKE
comparison. Native credential-store checks pass for Windows Credential Manager
and both macOS Keychain targets; Linux Secret Service remains untested without
a desktop session. These results supersede the foundation-only evidence for the
integrated implementation, while preserving the same limits on what a smoke test
establishes.

## Linux Secret Service follow-up

The [Linux native vault job](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38708142089/job/116332573128)
passed on 2026-10-15 at `f433e6c` (PR #101). It uses a real GNOME Keyring
service in an isolated D-Bus session with private temporary XDG directories.
The production adapter passes write/read/delete with normal backend selection;
a fresh process with an unreachable bus rejects storage without fallback.
This closes the source-level Linux native-service smoke gap in the earlier runs.

It does not establish interactive desktop unlock/dismissal behavior, alternate
Secret Service providers, or installed-manager native-vault acceptance. Installed
transfer evidence below still uses the explicitly selected encrypted-file vault.

## Integrated application evidence

The [application CI run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38471961663)
for `5bd37f6256599523de411e0ee6ffd4178ba7c143` passes on Python 3.10–3.13,
including lint/type checks, frontend production/static builds, and all desktop,
access-control and synchronization UI contract suites. Proxy concurrency tests
use explicit events rather than wall-clock sleeps.

The [update recovery matrix](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38471961581)
passes on Ubuntu, Windows, macOS Apple Silicon and macOS Intel at that commit.
Local implementation validation passed 2,007 tests with 8 environment skips
before the final installed-harness and UI-only follow-ups.

## Integrated browser evidence

The [dedicated native synchronization run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38463356209)
passed on Linux, Windows and macOS Apple Silicon for commit
`0e89885a92342e2e508742b7f71925e901c2d4dd`, using Camoufox
`v156.0.1-beta.36`. Two independent manager data directories on each runner transfer
a real browser profile through the production authenticated relay/client code and
back again. The test checks an HttpOnly authenticated cookie, localStorage changes,
pinned fingerprint and exact canvas output with **stable canvas explicitly enabled**.
Both values of the canvas privacy setting also roundtrip in unit tests; normal
session randomization is not silently disabled.

This establishes same-runner transfer behavior under the listed native platforms.
It does not establish identical rendering across every physical GPU, font stack or
managed desktop or cross-OS/architecture compatibility. Installed-manager GUI
acceptance is recorded separately below.

## Installed desktop acceptance

The [installed-manager run](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38471961607)
passed at `5bd37f6256599523de411e0ee6ffd4178ba7c143` on all four targets:

| Platform | Normal installer / frozen manager with bundled UI | Actual Camoufox workflow |
| --- | --- | --- |
| Ubuntu 22.04 x86-64 | Passed | Passed |
| Windows Server 2022 x86-64 | Passed | Passed |
| macOS 15 Apple Silicon | Passed | Passed |
| macOS 15 Intel | Passed | Passed |

Each runner creates two independent installed-manager processes and data
folders, an authenticated HTTPS relay, and an explicit encrypted-file key vault.
The bundled UI enables synchronization, pairs devices, imports the profile,
hands it over and retrieves it. Four real Camoufox launches verify HttpOnly
session cookies, localStorage updates, pinned fingerprint/browser settings and
exact stable-canvas output. The UI also publishes Trash, restores the profile,
schedules historical cleanup and cancels that signed decision. No permanent
historical collection is performed in this UI trial; grace and crash-safe
collection have separate deterministic relay tests.

Administrator bootstrap uses the real local API, so this run is not evidence
for every onboarding wizard interaction. Same-host, independent-installation
coverage does not prove behavior across every physical GPU, network or managed
OS configuration. Native-vault checks remain separate. The test's ephemeral CA
is provisioned only into disposable installed copies, never release artifacts;
TLS verification remains enabled. The extra cross-device restoration button
and its pending/unchanged-Trash regressions are covered by PR #98's frontend CI.

## Browser and runtime coverage

| Behavior | Evidence / limit |
| --- | --- |
| Signed relay requests, replay rejection, membership changes, checkout CAS | Automated unit/ASGI tests with real cryptography and persistent SQLite |
| Encrypted two-device handoff, immutable ciphertext and received snapshot validation | Runtime integration tests using real relay/client code and synthetic profile data |
| Crash recovery, local pointer activation, lease/maintenance fencing and stale rows | Fault-injection tests; native filesystem/lock behavior also requires the platform matrix on the final commit |
| Actual Camoufox cookie, localStorage, fingerprint and canvas transfer | Passed on Linux, Windows and macOS Apple Silicon in the dedicated run above; same runner, separate manager data directories |
| Installed manager transfer, Trash/restore and retention cancellation | Four native targets in the installed run above; encrypted-file vault, same runner |
| Historical recovery | Real cryptographic/archive negative tests and fresh local profile import tests; explicitly historical, with no recovered-current-checkout claim |
| Browser closed upload queue | Requires a running manager and accessible native vault; encrypted-file vault waits for explicit password entry |

## Supported boundaries

- Snapshots require the exact tested browser build, OS and architecture. Cross-OS,
  cross-architecture and browser-version migration are not silently attempted.
- Browsers must be closed for capture and activation. The manager uses local
  leases, native Firefox profile locks, durable checkout state and recovery fences.
  Direct modification of profile files outside these cooperating mechanisms is
  not prevented by encryption or the relay.
- SQLite/control/ciphertext directories require private permissions and a local
  filesystem with reliable locking, atomic operations and fsync. Network shares
  are outside the supported storage configuration.
- Native vault backends are explicitly allowlisted. Unavailable/locked vaults fail
  closed. Encrypted-file storage is an explicit password-based alternative, never
  an automatic plaintext fallback.
- Self-hosted relay traffic requires verified HTTPS. HTTP is restricted to explicit
  loopback development mode. The relay stores ciphertext and signed metadata;
  profile names, proxy credentials and browser state remain in encrypted snapshots.
- A malicious relay can withhold data or split isolated devices' views. Retained
  signed history and independently transferred checkpoints detect rollback against
  already observed state; they cannot prove global freshness after all independent
  witnesses are lost.
- Offline recovery creates a new historical local fork. It does not recall old
  browser sessions, restore old role grants or treat saved membership as current.
- Local extensions without a portable capability mapping block snapshot activation.
  Platform-specific codec/credential dependencies must be included in packaged
  builds; source-only success is insufficient release evidence.

Before describing synchronization as generally available, attach the final-commit
platform, packaged and actual-browser test results and resolve actionable security
review findings. Record any remaining untested environment explicitly.
