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
