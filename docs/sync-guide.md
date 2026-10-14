# Move a profile between your computers

[Русский](sync-guide.ru.md)

Synchronization is experimental and available in the **v0.6.0-rc.5 desktop preview**
for one owner and trusted personal devices. It is off until you explicitly set it
up and enable individual profiles.
It uses a self-hosted relay and transfers complete, closed profiles. It does not
require a vendor account. Download the installer for your platform from the
[rc.5 release](https://github.com/polyackiy/camoufox-profile-manager/releases/tag/v0.6.0-rc.5).
Sync dependencies are bundled; no Python or Node setup is required for the manager.
The relay remains separately hosted. Check the [tested matrix](sync-support-matrix.md).
An external security audit remains a stable-production release gate.

## Before setup

Use the same tested operating system, CPU architecture and Camoufox build on both
computers. Assigned extension-library add-ons currently block synchronization;
remove those assignments or retain the profile locally. The browser itself is
not patched. Other profiles continue to work without synchronization.

For a source checkout instead of a desktop installer:

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

## Recover a profile held by a lost device

Use the owner computer that still holds the owner-authority keys. An ordinary
paired computer cannot perform this action.

1. In **Settings → Device synchronization → Trusted devices**, select the missing
   device and choose **Revoke device**. Wait for publication; use **Resume
   membership update** if that step was interrupted.
2. Open **Device synchronization** in the affected profile's actions menu. When
   its state is **In use on another device**, choose **Recover from lost device**.
   Enter the unlock password first if you use an encrypted-file vault.
3. Read the warning and confirm **Recover revoked device profile**. The manager
   verifies signatures, decryption and snapshot compatibility before taking over,
   then installs the snapshot with a recovery journal.
4. Wait for **Ready on this device**. Review active sessions on the websites and
   revoke the missing device's website access as needed before reopening.

Recovery uses the last published compatible snapshot. Unsent changes that existed
only on the missing device are unavailable. A backup preserves this computer's
local unsent copy. Revocation and takeover cannot stop an offline browser or recall
existing website sessions. A history conflict, incompatible browser or unavailable
key keeps recovery blocked; preserve the data and use a separate local copy or
[historical recovery](sync-recovery-guide.md) instead of deleting either side.

Before publishing revocation, the manager makes a bounded attempt to verify
profile heads while the device is still authorized. If some heads cannot be
verified, revocation still proceeds and displays a recovery warning. An old-epoch
head advertised only after revocation is not automatically trusted; use an
independent retained checkpoint or explicit historical-copy recovery.

## Interrupted uploads

**Resume pending transfer** continues the same saved encrypted file from the last
server-confirmed byte offset. Keep the manager's private transfer directory intact.
A partial network chunk is retried; it never reuses an encryption stream for
changed browser data. Unfinished uploads expire after seven days. When the relay
confirms expiry and checkout/history still match, an explicit retry creates a new
revision with fresh encryption while retaining the original local ciphertext.

## Synchronized Trash and restore

Close the profile on its current holder, open **Device synchronization**, then
choose **Move synchronized profile to Trash** and confirm. This publishes a signed,
encrypted deletion version and releases the profile. Data, local access grants
and signed history remain available for recovery. Other computers learn about the
deletion when they retrieve its latest version; an offline browser cannot be stopped.

A receiving computer displays **Synchronized profile is in Trash** and blocks
opening. **Apply deletion on this device** requires confirmation and retains local
browser data in Trash. To recover, open **Trash → Device synchronization → Restore
synchronized profile**. Restore verifies the retained snapshot, backs up local
unsent data and publishes a new active version. Send that version to the other
device using the normal handoff. On a device whose copy remains in Trash, choose
**Check for restoration** to verify and install the active successor. If the latest
version is still deleted, local data remains in Trash.
If interrupted, use **Resume pending transfer** to finish the same confirmed
operation. Ordinary local restore and permanent deletion cannot bypass sync fences.

## Owner-approved historical storage cleanup

History is retained by default. On the owner-authority computer, open the profile's
**Device synchronization → Historical storage cleanup → Load verified historical
versions**. Select only old versions you no longer need, export any required
[offline recovery bundles](sync-recovery-guide.md), and confirm **Sign cleanup
decision**. Up to 64 exact historical versions can be selected in one decision.
The current version, signed receipts and local backups are never selected.

The relay waits at least seven days after receiving the decision and never acts
before the owner's signed earliest date. The displayed date is an earliest bound,
not a promise of when operator maintenance runs. **Cancel historical cleanup**
is available until collection begins; an unconfirmed cancellation remains pending
and must be retried. The operator runs the bounded collector described in
[sync-relay.md](sync-relay.md). Collected ciphertext cannot support historical
recovery without a separately saved bundle. Cleanup cannot erase copies that
other devices already downloaded, and relay status is not a global freshness proof.
