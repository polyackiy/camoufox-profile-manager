# Historical offline recovery

Losing every trusted device also loses the independent high-water marks that prove
which relay membership and profile head were last observed. A relay response and
an old encrypted recovery package cannot prove that no newer history exists.
This flow therefore creates a **historical offline copy**, never a continuation
of an old checkout and never a restored administrator/device identity.

Required material:

- The encrypted owner recovery package (raw package bytes) and its separately kept
  high-entropy recovery secret.
- The encrypted snapshot file.
- A recovery evidence JSON file with version 1, pinned `root`, every signed
  `memberships` record from genesis through the snapshot's membership epoch,
  signed `manifest`, and base64url `signature`.
- The exact supported browser build, operating system and CPU architecture.

`build_recovery_evidence(root, memberships, manifest, signature)` validates the
complete authority chain and snapshot writer before serializing evidence. Evidence
is limited to 64 MiB, 10,000 memberships and 16 levels of JSON nesting. Duplicate
keys, nonfinite numbers, missing ancestors and unknown fields fail closed.
The encrypted snapshot is subject to the normal snapshot size/file quotas.

From a source installation with the synchronization dependencies installed:

```sh
python -m camoufox_pm.sync.recovery_archive \
  --package owner-recovery.bin --evidence snapshot-evidence.json \
  --ciphertext snapshot.enc --output /private/recovery/new-copy \
  --browser-build EXACT-BUILD --os linux --architecture x86_64 \
  --acknowledge-historical-fork
```

The output parent must already be private to the operating-system account; the
output directory must not exist. The secret is requested through a concealed
terminal prompt, never through a command argument or environment variable.
A terminal unable to conceal input is rejected.

The recovery key decrypts the snapshot only after verification of the package's
root, full signed membership chain, authorized writer, signed manifest and exact
recipient set. Complete ciphertext hash and authenticated encryption are checked
before the archive is published. Snapshot path safety, quotas, portable metadata
and exact compatibility are validated before reporting success. Failures remove
only the newly created output; input files and existing destinations are retained.

Successful output contains `snapshot.zip` and `validated/data/`, with private
permissions. It contains sensitive browser state and profile metadata. It contains
no installed manager account, role grants, device keys, authority keys or checkout
permission. Import must assign a **fresh local profile ID**. If synchronization is
subsequently enabled, it must use a new remote profile/namespace and fresh keys.
Existing devices and the old relay namespace are unaffected and may still run.
The recovered archive may be old, or a signed snapshot never published as the
current head; this flow deliberately makes no freshness claim.

Maintaining the old namespace instead requires an independently authenticated
fresh membership/checkpoint witness from a surviving trusted device and a separate
recovery/revocation ceremony. That is not provided by this historical flow.
Automated adversarial tests are not an external cryptographic audit.
