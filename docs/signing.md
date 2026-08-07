# Code signing & notarization

The desktop builds (`.github/workflows/desktop.yml`) are **unsigned by default**.
Signing is enabled automatically once you add the secrets below — no workflow edits
needed. Each signing step is guarded, so builds keep working while secrets are
absent.

> These steps could not be verified in this repository (they need real
> certificates). They follow standard, documented practice — validate them on
> your first signed build.

Set secrets under **GitHub → repo → Settings → Secrets and variables → Actions**.

---

## macOS — Developer ID + notarization

Without this, macOS Gatekeeper blocks the app ("… is damaged / from an
unidentified developer").

### What you need

1. An **Apple Developer Program** membership ($99/year): <https://developer.apple.com/programs/>.
2. A **Developer ID Application** certificate (for distribution outside the App Store):
   - Xcode → Settings → Accounts → Manage Certificates → **+** → *Developer ID Application*, **or**
   - <https://developer.apple.com/account/resources/certificates> → **+** → *Developer ID Application*.
3. Export it as a `.p12` (Keychain Access → right-click the cert → *Export* → set a password).
4. A **notarization credential** — an App Store Connect API key is simplest:
   - <https://appstoreconnect.apple.com/access/integrations/api> → **+** → note the
     **Issuer ID**, the **Key ID**, and download the `.p8` (once).

### Secrets to add

| Secret | Value |
| ------ | ----- |
| `MACOS_CERT_P12` | `base64 -i cert.p12` (the .p12, base64-encoded) |
| `MACOS_CERT_PASSWORD` | the password you set when exporting the .p12 |
| `MACOS_SIGN_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_TEAM_ID` | your 10-character Team ID |
| `APPLE_API_KEY_ID` | the App Store Connect key ID |
| `APPLE_API_ISSUER_ID` | the App Store Connect issuer ID |
| `APPLE_API_KEY_P8` | `base64 -i AuthKey_XXXX.p8` (the .p8, base64-encoded) |

Encode a file for a secret with, e.g.:

```bash
base64 -i cert.p12 | pbcopy       # macOS: now paste into the secret
```

---

## Windows — Authenticode

Without this, SmartScreen warns users ("Windows protected your PC").

You have two good options:

### Option A — Azure Trusted Signing (recommended)

Microsoft's managed signing — cheap (~$10/month), no hardware token, works in CI.
Set it up at <https://learn.microsoft.com/azure/trusted-signing/>, then use the
`azure/trusted-signing-action`. This is the modern default; prefer it if you can.

### Option B — a `.pfx` certificate (OV)

Buy an **OV code-signing certificate** from a CA (Sectigo, DigiCert, …) that can be
exported as a `.pfx`. (EV certificates require a hardware token and cannot live in
CI.) Note: OV still shows a SmartScreen prompt until the app builds reputation.

| Secret | Value |
| ------ | ----- |
| `WINDOWS_CERT_PFX` | `base64 -w0 cert.pfx` (the .pfx, base64-encoded) |
| `WINDOWS_CERT_PASSWORD` | the .pfx password |

The workflow uses Option B (signtool) when `WINDOWS_CERT_PFX` is set. To use Option
A instead, swap the "Sign (Windows)" step for the Azure action.

---

## Linux

Linux has no mandatory signing. Distribute the `.tar.gz` (or build an AppImage and
optionally GPG-sign it, or publish to Flathub/Snap, which sign on their side). No
secrets are required.

---

## After adding the secrets

Run **Actions → Desktop builds → Run workflow** (optionally with a release tag).
The macOS job will produce a signed, notarized `.dmg` and the Windows job a signed
bundle; both are uploaded as artifacts and, if a tag is given, attached to that
release.
