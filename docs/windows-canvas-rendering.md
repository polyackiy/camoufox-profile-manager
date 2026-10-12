# Native canvas on a fresh Windows host

Tracked in [#73](https://github.com/polyackiy/camoufox-profile-manager/issues/73).
The per-profile `stable_canvas` option disables Firefox's baseline canvas
randomization. Pinned fonts help retain identity, but native text pixels also
depend on the browser and host renderer. The option does not guarantee identical
pixels after browser, font, driver or operating-system changes.

## Observed limit

On a fresh GitHub Windows runner, the first and second launches produced a
one-pixel difference in the same synthetic `16px Arial` drawing. The exact PNG
hash transition also occurred in **unmodified Playwright Firefox 153**, before
Camoufox or the manager used the renderer. A subsequent manager test passed with
a fresh profile. Another Python process and fresh profile directories retained
the later result; removing synthetic profile startup caches did not restore the
first result.

Evidence and downloadable synthetic artifacts:

- [Native Firefox control, followed by the unchanged manager test](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38319076724).
- [Independent Python processes on one Windows host](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38318561128).
- [Fresh profiles and profile-cache lifecycle](https://github.com/polyackiy/camoufox-profile-manager/actions/runs/38318099943).

This demonstrates a host-level first-use effect shared with native Firefox. It
does not establish which Windows cache or renderer operation causes it.
DirectWrite supports [shared caches](https://github.com/microsoft/WindowsAppSDK/blob/main/specs/dwritecore/DWriteCore.md),
but treating the FontCache service as a pixel-stability readiness signal would
require additional evidence. No manager warm-up launch, engine fork or speculative
rendering preference is used as a product workaround.

## Test boundary

Windows acceptance initializes the test host's renderer using at most three
independent native Firefox profiles. It saves every synthetic image and hash,
warns if the first result changes, and requires two consecutive equal results.
Nonconvergence fails setup. The manager suite then retains its exact canvas
restart assertion and three additional independent restart checks.

These are **renderer-initialized** acceptance checks. They do not cover an exact
cold-first-use pixel guarantee on a fresh Windows machine. The manual
`native-firefox` diagnostic bypasses preparation and preserves the strict native
comparison so maintainers can retest the known limitation. See the workflow's
manual inputs and [stage-two acceptance record](stage-two.md).

The profile option still changes a privacy tradeoff: disabling site/session noise
makes canvas output easier to correlate across sites. Native rendering changes
are a separate limitation, not a reason to silently regenerate the profile's
stored screen, GPU, CPU or font identity.
