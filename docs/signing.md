# Unsigned desktop release policy

The project releases **unsigned installers** and will not obtain platform
code-signing certificates. Publisher signing and macOS notarization are not
conditions for a preview or stable release. The maintainer confirmed this policy
on 21 October 2026 and closed [#61 as not planned](https://github.com/polyackiy/camoufox-profile-manager/issues/61).

The workflow creates macOS DMGs, the Windows installer and Linux packages without
signing credentials. Existing conditional signing steps remain implementation
code outside the current release process; configuring credentials or exercising
those steps is not release work.

## Expected package status

| Platform | Status to record |
| --- | --- |
| macOS Apple Silicon/Intel | No Developer ID publisher signature or notarization ticket. A packaging-generated ad-hoc signature is not publisher verification. |
| Windows x64 | No Authenticode publisher signature; `NotSigned` is expected. |
| Linux x64 | Unsigned `.deb` and portable `.tar.gz` downloads with SHA-256 sidecars; no signed apt repository. |

Record the actual status and tested architectures in release notes. Absence of a
publisher signature or notarization ticket is expected, not a failed release
check. For inspection, macOS `codesign -dv --verbose=4` and
`xcrun stapler validate` report identity/ticket status; Windows
`Get-AuthenticodeSignature` reports Authenticode status. These observations must
not be presented as verified publisher identity.

## Installation and integrity

Unsigned macOS apps may be blocked by Gatekeeper, and Windows may display
SmartScreen warnings. Organization policy can prevent opening unsigned software.
Record normal prompts and the user's installation decision; do not disable OS
security controls to make an acceptance test pass. See the
[English](getting-started.md) and [Russian](getting-started.ru.md) guides.

Download from the project's GitHub releases and compare each asset with its
same-name `.sha256` sidecar. These checks detect corruption; checksums from the
same release do not independently authenticate its publisher. Build success or
an ad-hoc macOS signature does not change that distinction.

## Release checks and automatic updates

[The release checklist](releasing.md#before-tagging) still requires tests,
packaging, installation/data-preservation evidence and a complete asset audit.
Keep untested configurations and observed failures explicit.

This release policy does not change the disabled automatic replacement path or
its cryptographic/publisher checks. The existing
[updater contract](update-signing.md) requires a separate reviewed trust and
acceptance decision before enablement. Unsigned stable releases continue to use
the manual installer flow in released versions. The unreleased
[native installer flow](native-installer-updates.md) adds explicit download,
backup and OS-installer handoff for Ubuntu and Windows without changing the
signed updater's trust checks or bypassing OS security policy.
