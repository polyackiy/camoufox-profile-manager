# Recover a historical profile without restoring old device authority

Recovery creates a **new local profile**. It preserves the authenticated snapshot's
cookies, persistent website sessions, fingerprint and proxy configuration. It does
not restore device keys, synchronization enrollment, users, profile grants or
schedules. The snapshot may be old: an offline bundle cannot prove the latest
membership, checkout or browser changes. Restoring it cannot close another device's
browser or revoke its website sessions.

Use the same Camoufox browser build, operating system and CPU architecture as the
snapshot. The recovery command checks the installed browser automatically and
refuses mismatches. It does not accept a command-line override of this check.
Extensions requiring an unsupported portable mapping remain blocked explicitly.

## Prepare a recovery bundle while a trusted device is available

1. In Settings → Device synchronization, export the encrypted owner recovery package.
   Keep its downloaded `.cpmrecovery` file. Save the separately displayed recovery
   secret somewhere else, then verify the saved file and secret in Settings.
2. Close the profile's browser. Complete any pending synchronization transfer.
3. Export a published encrypted revision and its signed evidence. Replace the
   database path and profile ID with those from your installation:

   ```sh
   camoufox-sync-recovery export \
     --db /path/to/data/profiles.db \
     --profile Abc123xy \
     --output /path/to/new-recovery-bundle
   ```

   The output directory must not already exist. The command requires a still-live
   local administrator enrollment, unlocked device keys and a reachable relay.
   An encrypted-file vault prompts for its unlock password; native vaults use the
   system credential store. The command exports the last authenticated published
   snapshot, not unsent edits in the browser directory.

The output contains `snapshot.ciphertext` and `evidence.json`. Neither contains
plaintext browser contents or the recovery secret. Copy these two files and the
owner's encrypted `.cpmrecovery` package to your backup storage. Keep the recovery
secret separately. Export a new bundle after important profile changes; the
command does not silently replace an earlier bundle.

## Restore offline on a clean or existing installation

Install the matching manager and Camoufox browser before disconnecting from the
network. Run:

```sh
camoufox-sync-recovery restore \
  --package /backup/camoufox-owner-recovery.cpmrecovery \
  --evidence /backup/new-recovery-bundle/evidence.json \
  --ciphertext /backup/new-recovery-bundle/snapshot.ciphertext \
  --db /path/to/restored-data/profiles.db \
  --acknowledge-historical-fork
```

Enter the separately saved owner recovery secret when prompted. Do not put it in
shell arguments, environment variables, URLs or scripts. Restore does not contact
the relay and does not require an existing synchronization enrollment. The target
database may be new or may already contain local profiles. Existing profiles and
their permissions are preserved; recovery prints the new local profile ID.

The target installation's adjacent `secret.key` protects imported proxy credentials.
If no key exists, a new one is created for a clean or unencrypted destination.
`CPM_SECRET_KEY`, when supplied, must agree with an existing adjacent key. An
existing encrypted database without its key is rejected; recover that installation's
key or choose a clean destination. Keep the target `secret.key` with its local data
backup, separately from the owner recovery secret.

Open the manager using the target data directory. Review the recovered profile and
website sessions before using it alongside any existing device. If you later enable
synchronization for this recovered copy, enroll a new owner/device as appropriate
and explicitly enable this new local profile. Never copy the old `sync-control`
directory or restore old checkout state as proof of ownership.

For a source checkout, the equivalent command is:

```sh
uv run --extra sync python -m camoufox_pm.sync.recovery_workflow --help
```

A validation, password, compatibility or maintenance failure preserves the source
package and ciphertext. Temporary plaintext staging is removed on handled failure.
A machine crash can leave a private `profiles/recovery-*` staging directory; after
confirming no recovery operation is running, remove that incomplete directory and
retry with the original encrypted inputs. Never delete an existing `profile_*`
directory merely because recovery failed.
