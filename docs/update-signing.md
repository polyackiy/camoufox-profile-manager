# Verified update repository contract

`core/update_targets.py` uses the maintained [python-tuf client](https://theupdateframework.readthedocs.io/en/latest/api/tuf.ngclient.updater.html).
Install the `updates` extra to use it. There is no production trust root in this
repository. Without an explicitly provisioned trusted local root, downloads fail
closed and users retain the manual installer flow. Never retrieve the first root
from the update repository or accept a root sent with an update request.

The caller owns a private persistent metadata directory, serializes access to it,
and preserves it across retries. TUF checks signatures, expiration, metadata
rollback and signed length/digests; the adapter bounds target size and metadata
limits. Staging receives only a verified target, and interrupted files are removed.
This does not establish platform publisher trust or install code.

Each authenticated target has `custom.camoufox_pm` containing exactly:
`protocol` (1), `component` (`manager` or `camoufox`), `version`, `platform`,
`architecture`, `channel` (`stable` or `preview`), `artifact` (exact target path),
`schema_min`, `schema_max`, `schema_result` (nonnegative integers), `recovery`
(`checkpoint-v1`), `minimum_updater`, `browser_version`, `publisher_policy`,
`product_id`, and `minimum_os`. Versions use PEP 440. Manager/browser pairing is
exact: the caller explicitly selects the required browser version. A manager
update never implicitly chooses another browser. Publishers must sign this
contract together with the target digest in TUF targets metadata.

Before production enablement, release owners must provision an offline-threshold
root, publish/rotate maintained TUF metadata, bind platform publisher identifiers,
and test signed package downloads, expiry, replay, target tampering and root
rotation. Policy values and repository URLs are trusted deployment configuration,
not values supplied by a remote release or HTTP request. macOS/Windows publisher
verification and transactional replacement remain separate mandatory gates.

## Platform publisher adapter

`core/update_publisher.py` supplies `verify_publisher(package, manifest, policy)`
for a helper's verification callback. `PublisherPolicy` is local trusted deployment
configuration. No policy or missing Team ID/certificate fingerprints refuses the
package. The caller must pass the manifest returned by TUF verification.

On macOS the adapter calls Apple's `codesign --verify --deep --strict
--all-architectures`, matches both signature identity and plist bundle identifier,
requires the configured Team ID and Developer ID Application authority, and
requires `spctl` to accept the package as Notarized Developer ID. `lipo` checks
that the signed target architecture is present in the actual main executable.
See [Apple's code signing details](https://developer.apple.com/library/technotes/tn2206/)
and [notarization troubleshooting](https://developer.apple.com/documentation/security/resolving-common-notarization-issues).

On Windows the adapter reads the PE architecture and calls the OS
[Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature)
trust verifier. Status must be `Valid`, the signer certificate thumbprint must
be in the locally configured rotation allowlist, and signed executable product
metadata must match. A certificate display name is never sufficient. This
adapter checks the configured main executable; it does not establish integrity
of adjacent DLLs or an installer. The platform integration must retain/recheck
TUF-authenticated package contents, authenticate an installer separately if one
is used, and invoke verification again immediately before replacement. Neither
adapter executes downloaded code. Unsupported platforms fail closed.

Unit tests mock OS results to exercise rejection paths. The native smoke test
uses the real OS tools to reject an unsigned synthetic package on macOS/Windows.
Positive signed and notarized package trials still require maintainer credentials
and remain release gates.

Target staging and metadata caches use the shared native permissions adapter:
Windows protected DACLs grant access only to the current user and SYSTEM; POSIX
paths use owner-only modes. Provisioned Windows root files must also satisfy this
private ACL policy. Persisted metadata is checked before reuse and protected after
refresh and delegated lookup. The only permitted cache symlink is python-tuf 7's
`root.json -> root_history/N.root.json`, contained inside the private metadata
cache with an owned, private regular-file destination. Arbitrary cache symlinks
and reparse-path escapes are rejected.

## Standalone tree transport (package protocol 1)

`packaging/build_update_package.py SOURCE OUTPUT --platform darwin|windows`
packages the final signed/notarized/stapled `.app` contents or Windows standalone
installation directory. SOURCE is the tree to replace: macOS starts at `Contents`,
and Windows includes the executable and **all adjacent DLLs/resources**. Produce
this ZIP after platform signing; publish its exact length and SHA-256/SHA-512 as
TUF target metadata. The producer is deterministic (fixed ZIP timestamps, sorted
paths, stored compression) and never supplies a trusted root or publisher policy.
Linux system packages continue through their package manager/manual installer.

The ZIP contains exactly `update-package.json` plus `payload/<path>` for every
file or symbolic link. Directories are declared in the inventory without ZIP
members. JSON is `{ "protocol": 1, "entries": [...] }`. Each entry has `path`,
`kind`, and `mode`; regular files add `length` and lowercase `sha256`, and links
add `target`. File modes are 0644 or 0755, directories 0755, links 0777. Windows
normalizes file modes to 0644 and uses inherited private native ACLs. Paths must
be portable relative names with no traversal, case/Unicode normalization
collisions, device names, or entries below a symlink. Only macOS accepts links:
relative, noncyclic, nondangling links that resolve inside the package tree.
This preserves PyInstaller framework links without following links during
extraction. Link text is represented as an ordinary ZIP member and bound to the
inventory. Devices, FIFOs, sockets, ZIP symlink attributes, encrypted members,
unknown inventory fields, extra members and duplicate names are rejected.

`extract_verified_package(VerifiedTarget, private_staging)` rechecks authenticated
archive length/hashes through the same descriptor used for ZIP parsing, then
verifies every extracted file's length/hash and link text. Limits are 2 GiB for
the archive, 8 GiB unpacked, 2 GiB per file, 50,000 inventory entries, 16 MiB for
inventory JSON, 32 MiB for the central directory and 1000:1 compression ratio.
The central directory is counted before ZIP's in-memory parsing; ZIP64 end
records and multi-disk archives are unsupported (ZIP64 local file headers are
permitted). Failed extraction removes the newly allocated staging tree.

The returned immutable `VerifiedPackage` binds the exact complete inventory.
`verify_tree(path=None)` rejects modified/missing/added files (including DLLs),
mode changes, symlink changes, special entries and Windows reparse points. The
integration must invoke this binding and the mandatory native publisher verifier
immediately before helper apply and against the installed tree before health
confirmation. A helper reconstructs its binding from a freshly authenticated
archive, never trusts an unsigned persisted inventory. TUF fixtures may use
short-lived CI signing keys and native verifier mocks to test this content
pipeline; they do not relax runtime publisher policy. Positive platform signature
and notarization trials still require release credentials.

For paired upstream Camoufox downloads, `extract_verified_browser_archive` accepts
only a separately TUF-authenticated `component=camoufox` target. The repository
can publish metadata for the exact upstream archive without redistributing it;
the trusted configured target base URL can point at upstream HTTPS hosting. The
raw ZIP needs no manager inventory manifest: the extractor derives a complete
immutable inventory from authenticated archive bytes, rechecks archive hashes
both before and after reading, and retains the upstream layout exactly (including
`Camoufox.app` if present). Directory entries can be implicit. Native ZIP symlinks
are permitted only for contained macOS framework links. All other extraction
bounds and path checks apply. Version/cache metadata and browser activation remain
owned by the paired-browser installer; locally added cache metadata is never
confused with the authenticated upstream inventory. No unsigned download URL or
local digest override is accepted by this pipeline.

## Local deployment policy

`CPM_UPDATE_POLICY` points to a provisioned local JSON file, not an HTTP input.
The runtime accepts exactly these top-level fields:

| Field | Meaning |
| --- | --- |
| `installation` | Absolute installed `.app` bundle or Windows standalone directory |
| `entrypoint` | Fixed relative executable, e.g. `Contents/MacOS/camoufox-pm` or `camoufox-pm.exe` |
| `trusted_root` | Absolute path to the independently provisioned manager TUF root |
| `metadata_url`, `targets_url` | HTTPS bases for manager metadata and authenticated package targets |
| `channels` | Object with exactly `stable` and `preview`, each a signed target path |
| `publisher` | Local `PublisherPolicy` fields: identifier, product_id, macos_team_id, windows_thumbprints, windows_executable |
| `schema`, `updater_version` | Installed schema compatibility and updater protocol implementation version |
| `browser` | Separate `trusted_root`, `metadata_url`, `targets_url`, and `artifact_template` with `{version}` for the exact paired upstream archive |

The policy must identify this installation. POSIX rejects writable-by-others
policy files; Windows requires a protected private ACL. Metadata and package
caches live in the private update-control directory beside the selected database.
The private policy is not a secret key store: TUF signing keys stay offline/outside
client deployments. Source installations, absent trust configuration and unsigned
platform publishers cannot enable automatic installation through an API switch.

Publish a stable manager target only after its explicitly paired browser passes
acceptance, even when upstream names that browser build `beta`. The stable channel
restriction applies to the manager release; it does not silently substitute a
newer upstream browser. Retained archives are authenticated again before applying,
and the full tree is verified before and after moving it into place.
