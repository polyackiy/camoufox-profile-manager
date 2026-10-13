"""Pure protocol guards, NOT durable checkout storage or browser fencing.

Inputs are trusted application data: membership, signatures, history proofs and
owner/device binding MUST be verified separately before calling these functions.
Every returned state must be durably committed before its permissions are used.
"""

from dataclasses import dataclass, replace
from enum import Enum


class TransitionRejected(ValueError):
    """The requested transition does not satisfy the reference protocol."""


class Phase(str, Enum):
    UNKNOWN = "freshness_unknown"
    HELD = "held_locally"
    ELSEWHERE = "held_elsewhere"
    RELEASED = "released"
    HANDOFF = "handoff_pending"
    CONFLICT = "conflict"
    REVOKED = "revoked"


@dataclass(frozen=True)
class Checkpoint:
    """Already authenticated checkpoint for one owner/profile binding."""

    owner_id: str
    profile_id: str
    epoch: int
    generation: int
    head: str
    holder: str | None

    def __post_init__(self) -> None:
        if not self.owner_id or not self.profile_id or not self.head:
            raise TransitionRejected("Checkpoint identity and head are required")
        if type(self.epoch) is not int or type(self.generation) is not int:
            raise TransitionRejected("Counters must be integers")
        if self.epoch < 1 or self.generation < 0 or self.holder == "":
            raise TransitionRejected("Invalid checkpoint counters or holder")


@dataclass(frozen=True)
class State:
    device_id: str
    checkpoint: Checkpoint | None = None
    phase: Phase = Phase.UNKNOWN
    observed_checkpoint: Checkpoint | None = None
    observed_heads: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.checkpoint is not None and self.observed_checkpoint is not None:
            _same_profile(self.checkpoint, self.observed_checkpoint)
        if not isinstance(self.phase, Phase):
            raise TransitionRejected("Unknown state phase")
        if not self.device_id:
            raise TransitionRejected("Device identity is required")
        if self.phase not in (Phase.UNKNOWN, Phase.REVOKED) and self.checkpoint is None:
            raise TransitionRejected("Known state requires a checkpoint")
        if self.phase in (Phase.HELD, Phase.HANDOFF):
            if self.checkpoint is None or self.checkpoint.holder != self.device_id:
                raise TransitionRejected("Local ownership does not match checkpoint")
            _check_authority_floor(self, self.checkpoint)
            if self.observed_checkpoint and self.observed_checkpoint.head != self.checkpoint.head:
                raise TransitionRejected("Local ownership has an unreconciled head")
            if self.observed_heads - {self.checkpoint.head}:
                raise TransitionRejected("Local ownership has unresolved history")
        if self.phase == Phase.ELSEWHERE and self.checkpoint:
            if self.checkpoint.holder is None or self.checkpoint.holder == self.device_id:
                raise TransitionRejected("Elsewhere state requires a different holder")
        if self.phase == Phase.RELEASED and self.checkpoint and self.checkpoint.holder:
            raise TransitionRejected("Released checkpoint still has a holder")


def may_work_offline(state: State) -> bool:
    """Only a durably confirmed holder may launch/mutate, subject to local locks."""
    return state.phase == Phase.HELD


def require_publication(state: State, expected: Checkpoint) -> None:
    """CAS guard; caller must ALSO refresh signed membership online."""
    if state.phase != Phase.HELD or state.checkpoint != expected:
        raise TransitionRejected("Publication requires confirmed ownership and exact CAS")


def invalidate(state: State) -> State:
    """Restore, missing control records or uncertainty never grants permission."""
    if state.phase == Phase.REVOKED:
        return state
    return replace(state, phase=Phase.UNKNOWN)


def revoke(state: State) -> State:
    """Sticky for this enrolled identity; re-enrollment is a separate protocol."""
    return replace(state, phase=Phase.REVOKED)


def observe(state: State, candidate: Checkpoint) -> State:
    """Detect stale/forked/new authority without granting ownership from a relay.

    A changed epoch/generation requires explicit trusted reconciliation. Any head
    change also requires authenticated ancestry; this function has none and blocks.
    """
    if state.phase == Phase.REVOKED:
        raise TransitionRejected("Revoked device")
    _check_authority_floor(state, candidate)
    previous = state.checkpoint
    observed = replace(
        state,
        phase=state.phase if candidate == previous else Phase.UNKNOWN,
        observed_checkpoint=candidate,
        observed_heads=state.observed_heads | {candidate.head},
    )
    if previous is None:
        return invalidate(observed)
    if candidate == previous:
        return observed  # Identical replies cannot clear a pending handoff.
    if candidate.epoch > previous.epoch or candidate.generation > previous.generation:
        return invalidate(observed)
    return replace(observed, phase=Phase.CONFLICT)


def reconcile(state: State, trusted: Checkpoint, *, ancestry: tuple[str, ...] = ()) -> State:
    """Accept an independently verified current checkpoint and history.

    Call only after online coordination AND trustworthy freshness confirmation;
    a relay response or valid signature alone is not this confirmation. `ancestry`
    is a previously verified revision chain, inclusive of old and new heads.
    S5 must preserve all conflicting/unsent data before calling this transition.
    """
    if state.phase == Phase.REVOKED:
        raise TransitionRejected("Revoked device")
    _check_authority_floor(state, trusted)
    observed_heads = state.observed_heads
    if state.observed_checkpoint is not None:
        observed_heads = observed_heads | {state.observed_checkpoint.head}
    for head in observed_heads - {trusted.head}:
        if head not in ancestry or not ancestry or ancestry[-1] != trusted.head:
            raise TransitionRejected("Observed head requires verified descendant history")
    old = state.checkpoint
    if old is not None:
        _same_profile(old, trusted)
        if trusted.epoch < old.epoch or trusted.generation < old.generation:
            raise TransitionRejected("Checkpoint rollback")
        if old.head != trusted.head:
            if len(ancestry) < 2 or ancestry[0] != old.head or ancestry[-1] != trusted.head:
                raise TransitionRejected("Verified descendant history required")
        if trusted.holder != old.holder and trusted.holder is not None:
            if trusted.generation <= old.generation:
                raise TransitionRejected("New checkout requires a new generation")
    phase = Phase.RELEASED if trusted.holder is None else Phase.ELSEWHERE
    if trusted.holder == state.device_id:
        phase = Phase.HELD
    return State(state.device_id, trusted, phase, trusted, frozenset({trusted.head}))


def begin_handoff(state: State) -> State:
    """Persist this intent BEFORE close/commit/release RPCs; it blocks new work."""
    if state.phase != Phase.HELD:
        raise TransitionRejected("Handoff requires local checkout")
    return replace(state, phase=Phase.HANDOFF)


def publication_ack(state: State, expected: Checkpoint, revision: str) -> State:
    """Apply an authenticated successful CAS reply for this exact operation.

    The caller proves browser quiescence, snapshot integrity, fresh membership and
    operation-ID binding. HANDOFF remains blocked even after a successful commit.
    """
    if state.phase not in (Phase.HELD, Phase.HANDOFF) or state.checkpoint != expected:
        raise TransitionRejected("Stale publication acknowledgement")
    if not revision or revision == expected.head:
        raise TransitionRejected("Publication needs a new revision")
    committed = replace(expected, head=revision)
    return replace(
        state,
        checkpoint=committed,
        observed_checkpoint=committed,
        observed_heads=frozenset({committed.head}),
    )


def release_ack(state: State, expected: Checkpoint) -> State:
    """Persist successful release for exactly the committed head/generation."""
    if state.phase != Phase.HANDOFF or state.checkpoint != expected:
        raise TransitionRejected("Release requires pending handoff and exact CAS")
    released = replace(expected, holder=None)
    return replace(state, checkpoint=released, observed_checkpoint=released, phase=Phase.RELEASED)


def _same_profile(old: Checkpoint, new: Checkpoint) -> None:
    if (old.owner_id, old.profile_id) != (new.owner_id, new.profile_id):
        raise TransitionRejected("Owner/profile binding mismatch")


def _check_authority_floor(state: State, candidate: Checkpoint) -> None:
    for known in (state.checkpoint, state.observed_checkpoint):
        if known is None:
            continue
        _same_profile(known, candidate)
        if candidate.epoch < known.epoch or candidate.generation < known.generation:
            raise TransitionRejected("Observed authority rollback")
        if candidate.generation == known.generation:
            if candidate.holder is not None and candidate.holder != known.holder:
                raise TransitionRejected("Observed checkout requires a new generation")
