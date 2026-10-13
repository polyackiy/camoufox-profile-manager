# Experimental snapshot protocol v1

Status: experimental wire protocol implemented by the opt-in synchronization
preview, originally specified in [S1 #83](https://github.com/polyackiy/camoufox-profile-manager/issues/83).
Enrollment, relay, checkout and profile application now integrate this format.
The outer manifest remains version 1; encrypted archive lifecycle metadata has
its own version boundary. This is not a production compatibility promise.
The threat model and release gates are in [stage-four-sync.md](stage-four-sync.md);
external cryptographic review and the remaining S8 acceptance gate are required
before general production enablement.

## Wire manifest

The manifest is a UTF-8 JSON object. Every field below is required, including the
explicit `null` parent of the first revision. Unknown fields and versions fail
closed. No profile names, paths, cookies, proxy secrets or local account data occur
in this object.

| Field | Representation |
| --- | --- |
| `version` | Integer `1` (not a boolean, float or string) |
| `suite` | `secretstream-xchacha20poly1305-hpke-x25519-sha256-chacha20poly1305-v1` |
| `owner_id`, `profile_id`, `revision_id`, `writer_device_id` | Non-nil lowercase hyphenated UUID strings |
| `parent_revision_id` | The preceding revision UUID or `null` for genesis |
| `membership_epoch`, `checkout_generation` | Integers 1 through 2^53−1 |
| `ciphertext_sha256` | Exactly 64 lowercase hexadecimal characters |
| `ciphertext_length` | Integer bytes, 41 through 2 GiB + 1 MiB |
| `recipients` | Ordered array of envelopes, as below |

A revision cannot be its own parent. IDs identify protocol entities, never local
filesystem objects. The length ceiling leaves bounded space for stream framing
above a maximum 2 GiB plaintext; the stream codec must enforce its own plaintext
and chunk limits independently. The lower bound accommodates a secretstream
header and an authenticated empty final message; additional container framing may
raise the actual smallest valid ciphertext.

Each recipient is exactly `{kind,recipient_id,enc,wrapped_key}`. Kind is `device`
or `recovery`; the ID follows the same UUID rules. `enc` is 32 bytes and
`wrapped_key` is 48 bytes, encoded as canonical unpadded base64url. Nonzero unused
base64 tail bits, alternate alphabets and padding are rejected. The fixed lengths
correspond to X25519 HPKE encapsulation and a 32-byte snapshot key with a 16-byte
ChaCha20-Poly1305 tag. These checks establish syntax, not cryptographic validity.

There must be 1–128 device recipients, including the writer, and exactly one
recovery recipient. Entries are strictly ordered lexicographically by `(kind,
recipient_id)`. Duplicate identities, including reuse across recipient kinds, are
invalid. The complete recipient list must equal independently authenticated
membership; presence in a manifest is not proof of authorization.

## Canonical bytes and domains

`canonical_bytes(manifest)` emits JSON with keys sorted lexicographically, no
whitespace, ASCII escaping enabled, and decimal integer counters. Valid values
contain only ASCII strings. Nested recipient keys follow the same ordering and
arrays retain their validated ordering. This deliberately restricted format does
not require general-purpose floating-point or Unicode canonicalization.

The signing input is the exact concatenation:

```
UTF8("camoufox-pm/sync/experimental-v1/manifest") || 0x00 || canonical_manifest
```

The signature therefore binds all ciphertext digest/length fields and every
recipient envelope. Verify against the writer key from authenticated owner
membership, never a key supplied by the same untrusted snapshot.

`encryption_context_bytes(manifest)` uses a different domain:

```
UTF8("camoufox-pm/sync/experimental-v1/snapshot") || 0x00 || canonical_context
```

The context contains every manifest field except `ciphertext_sha256`,
`ciphertext_length` and `recipients`; it adds `recipient_identities`, an ordered
array of `[kind,recipient_id]` pairs. Omitting encryption outputs avoids a circular
AAD dependency. Their integrity is supplied by the final signed manifest. Stream
AAD and the HPKE per-recipient info must incorporate this context; the crypto
codec additionally binds recipient identity and its own operation-specific domain.

These helpers perform serialization and validation only. No custom encryption,
signature, KDF or key agreement implementation belongs in this module.

## Resource bounds and validation order

`parse_manifest(bytes)` rejects payloads above 128 KiB before decoding, nesting
above eight levels before the JSON parser, duplicate object members at every
level, non-integer JSON numbers, invalid UTF-8, trailing documents, unknown keys
and wrong scalar types. It returns immutable `SnapshotManifest` /
`RecipientEnvelope` models. Applications must accept external bytes through this
parser rather than bypassing validation with Pydantic `model_construct` or
`model_copy(update=...)`.

The snapshot consumer must then:

1. Authenticate membership and the independent expected checkpoint. Unknown or
   recovered freshness is a blocked state, not permission to trust the relay.
2. Verify the signature using an authorized writer key from that membership.
3. Call `validate_context(manifest, SnapshotContext(...))` using expected owner,
   profile, selected revision, parent, writer, exact epoch/generation and exact recipient identities
   from trusted state. Values copied from the manifest provide no protection.
4. Check ciphertext byte length/digest and authenticate every stream message,
   including the final tag. Key unwrap alone does not validate the snapshot.
5. Validate the decrypted archive and compatibility in private staging before
   any activation. Commit a high-water mark only through the durable apply
   protocol and its crash recovery machinery.

Exact selected-revision comparison rejects substitution of a different signed
sibling snapshot sharing the same parent and counters. Exact parent comparison
rejects stale publications for a known head; neither check replaces authenticated history, replay state or a relay
transaction. A validly signed fork remains possible with a malicious relay. A
new device cannot infer freshness from a signature or counter alone. Device
revocation cannot recall already downloaded plaintext or keys.

## Executable checks

`tests/unit/test_sync_protocol.py` exercises deterministic bytes, distinct domains,
strict type and version rejection, nested duplicate members, resource bounds,
malformed and noncanonical recipient encodings, recipient ordering/membership,
self-parent rejection and cross-owner/profile/parent/writer/epoch/generation
substitution. Encryption-output changes alter signing bytes; recipient changes
also alter the encryption context. Crypto tampering/truncation and packaged
native-library checks are separate from structural protocol tests.
