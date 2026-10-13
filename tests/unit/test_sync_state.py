"""Executable negative vectors for the S1 reference (not runtime integration)."""

from dataclasses import FrozenInstanceError, replace

import pytest

from camoufox_pm.sync.state import (
    Checkpoint,
    Phase,
    State,
    TransitionRejected,
    begin_handoff,
    invalidate,
    may_work_offline,
    observe,
    publication_ack,
    reconcile,
    release_ack,
    require_publication,
    revoke,
)


@pytest.fixture
def held() -> State:
    return State("device-a", Checkpoint("owner", "profile", 2, 7, "head-1", "device-a"), Phase.HELD)


def test_models_are_immutable(held):
    with pytest.raises(FrozenInstanceError):
        held.phase = Phase.RELEASED


@pytest.mark.parametrize("phase", list(Phase))
def test_only_confirmed_holder_can_work_offline(held, phase):
    checkpoint = (
        replace(held.checkpoint, holder=None) if phase == Phase.RELEASED else held.checkpoint
    )
    if phase == Phase.ELSEWHERE:
        checkpoint = replace(held.checkpoint, holder="device-b")
    state = replace(held, phase=phase, checkpoint=checkpoint)
    assert may_work_offline(state) is (phase == Phase.HELD)


def test_sleep_or_clock_skew_cannot_steal_checkout(held):
    # There is intentionally no timestamp, heartbeat TTL or expiry transition.
    assert observe(held, held.checkpoint).checkpoint == held.checkpoint
    other = reconcile(State("device-b"), held.checkpoint)
    assert other.phase == Phase.ELSEWHERE
    assert not may_work_offline(other)
    with pytest.raises(TransitionRejected):
        begin_handoff(other)


@pytest.mark.parametrize("field,value", [("epoch", 3), ("generation", 8), ("head", "other")])
def test_publication_requires_exact_parent_epoch_generation(held, field, value):
    require_publication(held, held.checkpoint)
    with pytest.raises(TransitionRejected):
        require_publication(held, replace(held.checkpoint, **{field: value}))


def test_handoff_blocks_before_rpc_and_after_commit(held):
    pending = begin_handoff(held)
    assert not may_work_offline(pending)
    assert observe(pending, pending.checkpoint).phase == Phase.HANDOFF
    with pytest.raises(TransitionRejected):
        require_publication(pending, pending.checkpoint)
    committed = publication_ack(pending, held.checkpoint, "head-2")
    assert committed.phase == Phase.HANDOFF
    with pytest.raises(TransitionRejected):
        release_ack(committed, held.checkpoint)
    released = release_ack(committed, committed.checkpoint)
    assert released.phase == Phase.RELEASED
    assert not may_work_offline(released)
    assert released.checkpoint.holder is None


def test_release_cannot_skip_durable_intent(held):
    with pytest.raises(TransitionRejected):
        release_ack(held, held.checkpoint)


def test_uncertain_release_requires_explicit_reconciliation(held):
    pending = begin_handoff(held)
    remote = replace(held.checkpoint, holder=None)
    assert not may_work_offline(observe(pending, remote))
    assert reconcile(pending, remote).phase == Phase.RELEASED


@pytest.mark.parametrize("field,value", [("epoch", 1), ("generation", 6)])
def test_rollback_rejected_even_after_restore(held, field, value):
    restored = invalidate(held)
    old = replace(held.checkpoint, **{field: value})
    for operation in (observe, reconcile):
        with pytest.raises(TransitionRejected):
            operation(restored, old)


def test_missing_control_store_cannot_trust_relay_observation(held):
    missing = State("device-a")
    assert observe(missing, held.checkpoint).phase == Phase.UNKNOWN
    assert not may_work_offline(missing)


def test_lost_device_epoch_change_blocks_offline_use(held):
    takeover = replace(held.checkpoint, epoch=3, generation=8, holder="device-b")
    uncertain = observe(held, takeover)
    assert uncertain.phase == Phase.UNKNOWN
    assert uncertain.checkpoint == held.checkpoint
    assert reconcile(uncertain, takeover).phase == Phase.ELSEWHERE


def test_fork_preserves_old_head_and_blocks(held):
    fork = replace(held.checkpoint, head="fork")
    conflict = observe(held, fork)
    assert conflict.phase == Phase.CONFLICT
    assert conflict.checkpoint == held.checkpoint
    with pytest.raises(TransitionRejected):
        reconcile(conflict, fork)


def test_verified_ancestry_required_for_new_head(held):
    next_head = replace(held.checkpoint, head="head-3")
    with pytest.raises(TransitionRejected):
        reconcile(held, next_head, ancestry=("wrong", "head-3"))
    accepted = reconcile(held, next_head, ancestry=("head-1", "head-2", "head-3"))
    assert accepted.checkpoint == next_head


def test_new_checkout_must_increment_generation(held):
    released = release_ack(begin_handoff(held), held.checkpoint)
    with pytest.raises(TransitionRejected):
        reconcile(released, held.checkpoint)
    assert reconcile(released, replace(held.checkpoint, generation=8)).phase == Phase.HELD


def test_revocation_is_sticky(held):
    revoked = revoke(held)
    assert invalidate(revoked) == revoked
    for operation in (observe, reconcile):
        with pytest.raises(TransitionRejected):
            operation(revoked, held.checkpoint)
    for operation in (begin_handoff,):
        with pytest.raises(TransitionRejected):
            operation(revoked)
    with pytest.raises(TransitionRejected):
        publication_ack(revoked, held.checkpoint, "head-2")


@pytest.mark.parametrize("field", ["owner_id", "profile_id"])
def test_cross_owner_or_profile_rejected(held, field):
    wrong = replace(held.checkpoint, **{field: "another"})
    for operation in (observe, reconcile):
        with pytest.raises(TransitionRejected):
            operation(held, wrong)


@pytest.mark.parametrize(
    "field,value", [("epoch", True), ("epoch", 0), ("generation", -1), ("holder", "")]
)
def test_bad_checkpoint_rejected(held, field, value):
    with pytest.raises(TransitionRejected):
        replace(held.checkpoint, **{field: value})


@pytest.mark.parametrize(
    "phase,holder",
    [
        (Phase.HELD, None),
        (Phase.HANDOFF, "device-b"),
        (Phase.ELSEWHERE, None),
        (Phase.ELSEWHERE, "device-a"),
        (Phase.RELEASED, "device-a"),
    ],
)
def test_inconsistent_permission_state_rejected(held, phase, holder):
    with pytest.raises(TransitionRejected):
        replace(held, phase=phase, checkpoint=replace(held.checkpoint, holder=holder))


@pytest.mark.parametrize("phase", [Phase.UNKNOWN, Phase.RELEASED, Phase.CONFLICT])
def test_delayed_commit_ack_cannot_reenable_uncertain_state(held, phase):
    checkpoint = (
        replace(held.checkpoint, holder=None) if phase == Phase.RELEASED else held.checkpoint
    )
    blocked = replace(held, checkpoint=checkpoint, phase=phase)
    with pytest.raises(TransitionRejected):
        publication_ack(blocked, checkpoint, "head-2")


def test_repeat_old_commit_ack_rejected(held):
    committed = publication_ack(held, held.checkpoint, "head-2")
    with pytest.raises(TransitionRejected):
        publication_ack(committed, held.checkpoint, "head-2")
    assert committed.checkpoint.head == "head-2"


@pytest.mark.parametrize("field,value", [("epoch", 3), ("generation", 8)])
def test_observed_authority_floor_survives_unknown_and_restore(held, field, value):
    newer = replace(held.checkpoint, **{field: value})
    unknown = observe(held, newer)
    assert unknown.phase == Phase.UNKNOWN
    assert unknown.checkpoint == held.checkpoint
    assert unknown.observed_checkpoint == newer
    for blocked in (unknown, invalidate(unknown)):
        for operation in (observe, reconcile):
            with pytest.raises(TransitionRejected):
                operation(blocked, held.checkpoint)
        assert not may_work_offline(blocked)


def test_observed_release_prevents_same_generation_holder_resurrection(held):
    released = replace(held.checkpoint, holder=None)
    uncertain = observe(begin_handoff(held), released)
    for operation in (observe, reconcile):
        with pytest.raises(TransitionRejected):
            operation(uncertain, held.checkpoint)
    assert reconcile(uncertain, released).phase == Phase.RELEASED
    assert reconcile(uncertain, replace(held.checkpoint, generation=8)).phase == Phase.HELD


def test_observed_head_is_not_discarded_by_reconciliation(held):
    newer = replace(held.checkpoint, generation=8, head="head-2")
    unknown = observe(held, newer)
    with pytest.raises(TransitionRejected):
        reconcile(unknown, replace(held.checkpoint, generation=8))
    with pytest.raises(TransitionRejected):
        reconcile(unknown, newer)
    accepted = reconcile(unknown, newer, ancestry=("head-1", "head-2"))
    assert accepted.phase == Phase.HELD


def test_observed_floor_without_applied_checkpoint_is_retained(held):
    unknown = observe(State("device-a"), held.checkpoint)
    assert unknown.checkpoint is None
    assert unknown.observed_checkpoint == held.checkpoint
    with pytest.raises(TransitionRejected):
        reconcile(unknown, replace(held.checkpoint, epoch=1))


def test_repeated_observation_cannot_erase_unreconciled_fork(held):
    first = replace(held.checkpoint, head="branch-a")
    second = replace(held.checkpoint, head="branch-b")
    conflict = observe(observe(held, first), second)
    with pytest.raises(TransitionRejected):
        reconcile(conflict, second, ancestry=("head-1", "branch-b"))
    replayed = observe(conflict, held.checkpoint)
    with pytest.raises(TransitionRejected):
        reconcile(replayed, held.checkpoint)


def test_constructor_cannot_grant_ownership_below_observed_floor(held):
    with pytest.raises(TransitionRejected):
        replace(held, observed_checkpoint=replace(held.checkpoint, epoch=3))
    with pytest.raises(TransitionRejected):
        replace(held, observed_heads=frozenset({"unreconciled-head"}))
