# Synchronization product acceptance

Reviewed official product documentation on 2026-10-13. These are interaction
patterns and acceptance criteria, not copied code or claims of feature parity.

| Reference | Useful behavior | Our acceptance criterion |
| --- | --- | --- |
| [GoLogin FAQ](https://gologin.com/faq/) | Session lock and moving profiles across devices | A profile held on another device cannot start locally; a timeout never steals ownership. |
| [AdsPower global settings](https://help.adspower.com/docs/global_settings) | Upload after closing, explicit completion before opening elsewhere | Closing queues synchronization; the UI distinguishes local changes, transfer in progress, transferred, offline and failed. A failed upload retains local data. |
| [Dolphin profile cloud sync](https://docs.dolphin-anty.com/en/working-with-profiles/profile-cloud-sync) | Per-profile opt-in and safeguards when disabling sync | Enabling/disabling requires a closed profile and an available current local copy. Disabling cannot delete the only copy or silently discard pending changes. |
| [Multilogin backup profile syncing](https://multilogin.com/glossary/backup-profile-syncing/) | Portable profile backups and continuity between devices | An authenticated snapshot can be restored into a separate preserved copy; conflicts never use silent last-write-wins replacement. |

The initial supported scope is one owner's trusted devices. Local team roles do
not automatically become remote key grants. The relay receives encrypted browser
snapshots and opaque routing identifiers, not local administrator accounts or
manager databases. See [the trust contract](sync-trust-contract.md).

## Required user flows

1. An administrator explicitly creates an owner, chooses protected key storage,
   saves the offline recovery package and separate secret, and verifies recovery.
2. A second computer creates its own keys. The owner compares the full fingerprint
   through a trusted channel and signs a single-use approval of that device request.
   Pairing request and approval travel through the trusted channel, not the relay.
3. The user chooses a closed profile and sees which trusted devices will receive
   its secrets. The initial complete encrypted snapshot is published atomically.
4. The current holder closes the browser and hands it over. The second computer
   validates compatibility and the authenticated snapshot before replacing data.
5. Interrupted transfers and application restarts retain progress and ownership
   uncertainty. The UI offers retry and preservation of a separate local copy.
6. Device revocation advances membership. New snapshots omit revoked recipients;
   already downloaded browser sessions cannot be remotely erased.

## Honest limitations

Initially require the same tested OS, architecture and browser build. Explain
compatibility failures without changing the fingerprint automatically. Offline
work is allowed only for the durably confirmed holder; recovery from old backups
requires trusted freshness reconciliation. Encryption cannot make an unavailable
relay reachable, or prove the newest global history against an isolated malicious
relay. Stable production claims require the security acceptance recorded in
[the review checklist](sync-security-review.md).
