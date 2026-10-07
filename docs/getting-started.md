# Getting started

[Русский](getting-started.ru.md)

## Download and open

Open the [releases page](https://github.com/polyackiy/camoufox-profile-manager/releases)
and choose a desktop installer from a release's **Assets**. Python, Node.js and a
terminal are unnecessary for desktop packages. Installer builds are attached by
the release workflow; older releases may only contain Python packages.

| Computer | File | Installation |
| --- | --- | --- |
| Mac with Apple Silicon (M1 or newer) | `camoufox-pm-macos-arm64.dmg` | Open the disk image, drag the app into Applications, then open it there. |
| Mac with Intel processor | `camoufox-pm-macos-x86_64.dmg` | Open the disk image, drag the app into Applications, then open it there. |
| Windows 10/11, 64-bit Intel/AMD | `camoufox-pm-windows-x86_64-setup.exe` | Run the installer, then open the app from the Start menu. Installs for your account. |
| Ubuntu 22.04+ or compatible Debian, 64-bit Intel/AMD | `camoufox-pm-linux-x86_64.deb` | Open with the system software installer, then launch from Applications. |

If Microsoft WebView2 is missing on Windows, the installer downloads it through
Microsoft’s official runtime bootstrapper; this needs internet access.

Linux also has a portable `.tar.gz`: extract the complete folder and open its
`camoufox-pm` executable. Keep the `_internal` directory beside it. The desktop
webview needs system graphics libraries; use the `.deb` where possible because
it declares those dependencies. Other Linux distributions have not been validated.

Unsigned builds can trigger macOS Gatekeeper or Windows SmartScreen. Check the
release's signing status and publisher before deciding to open it. Managed
computers may require your administrator's approval. Do not turn off your system's
security features. See [signing status and limitations](signing.md).

## First profile

1. Open **Camoufox Profile Manager**. It keeps your data separately from the installed app.
2. Choose **Install browser** in the setup banner. Internet access is needed for
   this initial download. Progress and errors appear in the app; if downloading
   fails, retry. **Retry download** is also available in Settings.
3. Choose **Create first profile** or **New profile**. Give it a name you recognize.
4. Leave the advanced fingerprint settings at their defaults. Add a proxy only if
   you already have one; check it before relying on its location.
5. Click **Run**. Your profile opens in a separate Camoufox browser window. Close
   that window or click **Stop** when finished. Closing the manager also stops its
   running browser sessions.

Your profile retains its cookies, history and fingerprint between launches. A
clone represents a different machine by default. Regenerating a fingerprint
changes the apparent machine: read the confirmation before doing it.

## Protect and recover your data

**Settings → Profile backups → Back up profiles** creates a recovery archive.
Stop your profiles first; running or leased profiles are skipped and retried later.
Keep a copy outside this computer. Restore a backup with **Restore as new
profile**; it creates a separate profile so the current one is preserved.

Deleting a profile moves it to **Trash**. Use **Restore** there to bring it back.
**Delete permanently** asks you to type its name and removes its browser data;
recovery then requires an earlier backup. Protect backup archives as carefully
as the profiles: archives are unencrypted and can contain signed-in sessions,
cookies, history and proxy passwords. Profile archives do not back up login
accounts, sessions for this manager, or global application settings. Restoring a
profile archive does not require the original encryption key; imported proxy
passwords are encrypted with the current key. Keep the original database and key
when preserving the whole installation. Automatic backups default to every 24 hours,
retaining seven routine archives and two update checkpoints per profile.
Trash has no automatic expiry; restoring keeps profile schedules paused.

**Settings → App updates → Check for updates** checks GitHub for a newer release.
Stop running profiles before preparing an update.
**Back up and prepare update** creates a backup before offering the release
downloads. Choose the installer for your computer. The release link is checked to belong to this project; this is not
a cryptographic verification of the installer. Download it, close the manager, and install
it over the existing app. The app does not replace its executable automatically.
A backup must succeed before update preparation continues.

## Where data lives

The desktop app uses the same folder regardless of where you launch it:

| System | Default data folder |
| --- | --- |
| macOS | `~/Library/Application Support/Camoufox Profile Manager/` |
| Windows | `%LOCALAPPDATA%\Camoufox Profile Manager\` |
| Linux | `$XDG_DATA_HOME/camoufox-profile-manager/` or `~/.local/share/camoufox-profile-manager/` |

Keep the whole folder, including `profiles.db`, browser data, backups and
`secret.key`. A fresh desktop install generates this encryption key; losing it
can make encrypted proxy passwords unreadable. Reinstalling the app or uninstalling
the Windows program keeps the separate data folder. CLI/source installations
continue to use `data/profiles.db` unless configured otherwise; their existing
data is not silently moved into the desktop folder.

The manager's server listens on this computer only and chooses an available
local port. If it says **already open**, switch to its existing window. For a
startup failure, `desktop.log` in the data folder records output from packaged
windowed builds. Do not share logs or backups publicly without checking for
private information.

## From source (developers)

Install [uv](https://docs.astral.sh/uv/) and Node.js 20.9+; uv manages Python.

```bash
git clone https://github.com/polyackiy/camoufox-profile-manager.git
cd camoufox-profile-manager
uv sync --extra desktop
uv run python scripts/build_webui.py
uv run python -m camoufox_pm.desktop
```

On Linux, source desktop mode also needs a supported pywebview GUI backend and
its system libraries. The packaged `.deb` bundles the Qt backend. For a web tab
instead, use `uv sync`, build the UI, and run `uv run camoufox-pm`.
[CLI options](cli.md) and [profile settings](profile-settings.md) cover advanced use.
