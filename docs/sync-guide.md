# Move a profile between your computers

[Русский](sync-guide.ru.md)

Synchronization is a source preview for one owner and trusted personal devices.
It uses a self-hosted relay and transfers complete, closed profiles. It does not
require a vendor account. Existing desktop release assets predate this feature;
check the release notes and [tested matrix](sync-support-matrix.md) before installing.
An external security audit remains a stable-production release gate.

## Before setup

Use the same tested operating system, CPU architecture and Camoufox build on both
computers. Assigned extension-library add-ons currently block synchronization;
remove those assignments or retain the profile locally. The browser itself is
not patched. Other profiles continue to work without synchronization.

Install the optional dependencies in a source checkout:

```sh
uv sync --extra sync --extra dev
uv run --no-sync python scripts/build_webui.py
uv run --no-sync camoufox-pm user add owner
uv run --no-sync camoufox-pm
```

Use an administrator account on each computer. A manager API key or anonymous
loopback access cannot enroll devices. Configure your relay using the
[operator guide](sync-relay.md), and keep its HTTPS address and registration token
ready. The relay stores encrypted snapshots, not browser passwords in plaintext.

## Set up the first computer

1. Open **Settings → Device synchronization**. Enter the relay address and a
   recognizable device name.
2. Choose **System credential store**. If it is unavailable, explicitly choose
   **Encrypted file with an unlock password** and keep that password safe.
3. Export the encrypted owner recovery package. Save its recovery secret
   separately: the downloaded package deliberately does not contain the secret.
4. Select the saved package and enter the separately saved secret. Verification
   restores the keys in an isolated process before enrollment becomes ready.
5. Open **Connect your relay** and enter its registration token.

Only the first computer holds owner-authority keys. Ordinary paired computers
receive their own device keys. Losing all keys and the recovery package/secret
cannot be fixed by resetting a relay password.

## Connect another computer

1. Copy the owner's connection code from Settings to the new computer.
2. On the new computer, prepare a device connection and send its request code
   back to the owner.
3. Compare the **entire fingerprint** through a separate trusted channel. The
   owner approves only the matching request and returns the approved code.
4. Confirm the matching fingerprint on the new computer and accept the code.

Interrupted setup retains its request and keys. Use **Resume saved device
connection** or **Resume membership update** rather than starting over. Device
revocation prevents future authorized relay access; it cannot erase sessions
already downloaded or stop a disconnected browser.

## Send and receive a profile

1. Close the profile on its current computer. In its actions menu, open **Device
   synchronization**, consent to sharing its sessions with trusted devices, and
   enable synchronization.
2. Choose **Send to another device**. Wait until the state says **Ready for
   another device**. Closing a synchronized browser queues this handoff; native
   key stores allow background transfer while the manager remains open. An
   encrypted-file vault waits for your unlock password. Interrupted transfers
   remain queued across restarts.
3. On the receiving computer, open **Profiles from trusted devices**, add the
   trusted profile, then choose **Get latest profile**. Open it only when the
   state says **Ready on this device**.
4. Close it and send it back when finished. A disconnected device does not lose
   its checkout merely because a timeout expired.

A newly paired device needs a fresh snapshot containing its encryption key. Send
that profile again from an existing device after pairing. Unsupported browser
builds and missing key envelopes are rejected before taking ownership.

Profiles created after pairing require an updated owner-approved profile-list
code. The owner shares that code with the intended device; accepting it adds new
trusted profile anchors without replacing device keys or existing local profiles.

Profile names, cookies, local storage, fingerprint and proxy settings travel
inside encryption. Manager users, roles, export grants, schedules, local paths
and updater state do not travel. Imported profiles receive new local IDs; the
remote identity stays bound to the verified snapshot history.

## If something interrupts the transfer

- **Awaiting confirmation / handoff:** resume the pending transfer. The same
  encrypted file and revision are retried; the source is retained.
- **Latest profile must be installed:** get the latest profile, or recover an
  interrupted installation. The previous local directory remains available.
- **In use on another device:** close and send it from that device. Do not remove
  local locks or copy its database as a workaround.
- **Conflict / revoked / unknown state:** preserve both sides. **Preserve as a new
  local copy** explicitly creates an independent local recovery copy; it does
  not resolve ownership and another copy of that browser identity may still run.
- **After restoring an older manager checkpoint:** synchronize again before
  opening affected profiles. Restoring data does not restore old checkout rights.

Keep an encrypted offline snapshot bundle as well as the recovery package. See
[export and fresh-install recovery](sync-recovery-guide.md). A recovered historical
copy is not proof of the latest remote state. The relay's encrypted storage also
needs operator backups; restoring an old relay backup must not erase clients'
independent history.
