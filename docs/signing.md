# Desktop signing and notarization

Desktop installers are **unsigned by default**. The workflow always creates
DMGs, the Windows installer, and Linux packages even when credentials are absent.
Do not call an artifact signed or notarized solely because the workflow passed:
inspect which optional signing steps ran and verify the downloaded package.
No signing certificates or notarization credentials were available during this
implementation; those paths require a real signed-build verification.

Unsigned macOS apps may be blocked by Gatekeeper, and Windows may display
SmartScreen warnings. Organization policy can prevent opening unsigned software.
The beginner guide does not advise disabling security controls. Signing does
not guarantee an immediate absence of reputation-based Windows prompts.

## macOS

Add repository Actions secrets:

| Secret | Meaning |
| --- | --- |
| `MACOS_CERT_P12` | Base64-encoded Developer ID Application certificate and private key |
| `MACOS_CERT_PASSWORD` | Password protecting the certificate export |
| `MACOS_SIGN_IDENTITY` | Full Developer ID Application identity |
| `APPLE_API_KEY_P8` | Base64-encoded App Store Connect API private key |
| `APPLE_API_KEY_ID` | API key ID |
| `APPLE_API_ISSUER_ID` | API issuer ID |

The workflow imports the certificate into a temporary keychain, signs the app
with the hardened runtime and timestamp, creates a DMG, submits it with
`notarytool`, then staples the ticket. Notarization runs only when both the
certificate and API private key are present; signing alone is not notarization.
Temporary credential files/keychain are removed in an `always()` cleanup step.

On the first credentialed run, verify nested Python/native libraries are signed,
inspect notarization diagnostics, and test the downloaded DMG on a clean Mac:

```bash
codesign --verify --deep --strict --verbose=2 "/Applications/Camoufox Profile Manager.app"
spctl --assess --type execute --verbose "/Applications/Camoufox Profile Manager.app"
xcrun stapler validate camoufox-pm-macos-arm64.dmg
```

See [Apple's notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
for account requirements and troubleshooting. Both architectures need verification.

## Windows

Add `WINDOWS_CERT_PFX` (base64-encoded Authenticode certificate/private key) and
`WINDOWS_CERT_PASSWORD`. The workflow signs the application executable before
NSIS packaging, then signs the final installer, using SHA-256 and a timestamp.
The Windows SDK supplies `signtool`. Verify both downloaded installer and
installed executable with `signtool verify /pa /v`.

This path applies only to signing credentials that can be used as a PFX in CI.
Hardware-backed or managed signing services need a corresponding integration;
the workflow does not claim to support them automatically. Follow
[Microsoft's SignTool documentation](https://learn.microsoft.com/windows/win32/seccrypto/signtool)
for certificate and verification requirements.

## Linux

The `.deb` and portable `.tar.gz` are unsigned and accompanied by SHA-256 sidecars.
They are release downloads, not a signed apt repository. GPG signatures, package
repository distribution and AppImage are future work.

## Release labels

Record the actual status in release notes: unsigned, signed, or signed and
notarized, and which architectures/platforms were tested. A local macOS build or
mocked frontend check does not establish Windows/Linux installation success.
[Release checks](releasing.md) list the outstanding real-system checks.
