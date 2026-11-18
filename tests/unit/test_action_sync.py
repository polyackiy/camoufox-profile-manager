"""The browser manager never releases a profile while accepted actions can run."""

import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_pm.core.browser_session import BrowserSession, BrowserSessionManager
from tests.unit.test_action_capture import event, gate, signed


def prepare(*dispatchers, authorize=None, timeout=1.0, capacity=2, use_preflight=False):
    storage = SimpleNamespace(release_lease=AsyncMock())
    manager = BrowserSessionManager(storage, "owner")
    leader = BrowserSession("leader", None)
    followers = [BrowserSession(f"follower-{index}", None) for index in range(len(dispatchers))]
    manager.active_sessions.update((item.profile_id, item) for item in (leader, *followers))

    async def preflight(dispatch, click):
        return partial(dispatch, click)

    if not use_preflight:
        dispatchers = tuple(partial(preflight, dispatch) for dispatch in dispatchers)
    capture = gate()
    run = manager.action_sync.prepare(
        capture,
        leader,
        tuple(zip(followers, dispatchers, strict=True)),
        authorize or allow,
        dispatch_timeout=timeout,
        queue_capacity=capacity,
    )
    return manager, run, capture, leader, followers


async def allow(_target):
    return True


def submit(run, seq):
    value = {**event(), "sequence": seq}
    return run.submit(value, signed(value))


@pytest.mark.asyncio
async def test_explicit_start_and_order_per_follower():
    seen = [[], []]
    done = asyncio.Event()

    async def dispatch(index, payload):
        seen[index].append(payload.sequence)
        if sum(map(len, seen)) == 4:
            done.set()

    _, run, _, _, _ = prepare(partial(dispatch, 0), partial(dispatch, 1))
    assert not submit(run, 1)
    try:
        assert await run.start()
        assert submit(run, 1)
        assert submit(run, 2)
        await asyncio.wait_for(done.wait(), 1)
        assert seen == [[1, 2], [1, 2]]
    finally:
        assert await run.stop(timeout=1)
    assert not await run.start()
    assert not submit(run, 3)


@pytest.mark.asyncio
async def test_revocation_during_dispatch_check_clears_queue():
    entered, revoked = asyncio.Event(), asyncio.Event()
    checks = 0

    async def authorize(_target):
        nonlocal checks
        checks += 1
        if checks == 1:
            return True
        entered.set()
        await revoked.wait()
        return False

    dispatch = AsyncMock()
    _, run, _, _, _ = prepare(dispatch, authorize=authorize)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert submit(run, 2)
        revoked.set()
        await asyncio.wait_for(run.stopped.wait(), 1)
        dispatch.assert_not_awaited()
        assert run.reason == "authority_lost"
    finally:
        revoked.set()
        assert await run.stop(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", [False, True])
async def test_timeout_and_overflow_retain_reservation_until_dispatch_returns(overflow):
    entered, release = asyncio.Event(), asyncio.Event()
    dispatched = []

    async def dispatch(payload):
        dispatched.append(payload.sequence)
        entered.set()
        await release.wait()

    manager, run, _, leader, (follower,) = prepare(dispatch, timeout=0.05, capacity=1)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert submit(run, 2)
        if overflow:
            assert not submit(run, 3)
        await asyncio.wait_for(run.draining.wait(), 1)
        assert run.reason == ("overflow" if overflow else "deadline")
        assert not await run.stop(timeout=0.01)
        assert manager.action_sync.for_browser(leader) is run
        with pytest.raises(RuntimeError):
            manager.action_sync.prepare(gate(), leader, ((follower, dispatch),), allow)
    finally:
        release.set()
        assert await run.stop(timeout=1)
    assert dispatched == [1]
    assert manager.action_sync.for_browser(leader) is None


@pytest.mark.asyncio
async def test_close_and_repeated_cancellation_wait_before_releasing_browser_lease():
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(_payload):
        entered.set()
        await release.wait()

    manager, run, _, leader, (follower,) = prepare(dispatch)
    closing = None
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert submit(run, 2)
        closing = asyncio.create_task(manager.close_and_release(follower.profile_id))
        await asyncio.wait_for(run.draining.wait(), 1)
        for _ in range(2):
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
            manager._storage.release_lease.assert_not_awaited()
        assert manager.active_sessions[follower.profile_id] is follower
        assert not submit(run, 3)
    finally:
        release.set()
        if closing is not None:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await run.stop(timeout=1)
    manager._storage.release_lease.assert_awaited_once_with(follower.profile_id, "owner")
    assert manager.action_sync.for_browser(leader) is None


@pytest.mark.asyncio
async def test_replaced_browser_cannot_receive_an_action_that_passed_authority():
    entered, release = asyncio.Event(), asyncio.Event()
    checks = 0

    async def authorize(_target):
        nonlocal checks
        checks += 1
        if checks == 2:
            entered.set()
            await release.wait()
        return True

    dispatch = AsyncMock()
    manager, run, _, _, (follower,) = prepare(dispatch, authorize=authorize)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        manager.active_sessions[follower.profile_id] = BrowserSession(follower.profile_id, None)
        release.set()
        await asyncio.wait_for(run.stopped.wait(), 1)
        assert run.reason == "browser_changed"
        dispatch.assert_not_awaited()
    finally:
        release.set()
        assert await run.stop(timeout=1)


@pytest.mark.asyncio
async def test_old_signed_sequence_stops_stream_without_replay():
    seen = []
    dispatched = asyncio.Event()

    async def dispatch(click):
        seen.append(click.sequence)
        dispatched.set()

    _, run, _, _, _ = prepare(dispatch)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(dispatched.wait(), 1)
        assert not submit(run, 1)
        assert not submit(run, 2)
        assert run.reason == "capture_rejected"
    finally:
        assert await run.stop(timeout=1)
    assert seen == [1]


@pytest.mark.asyncio
async def test_cancelled_worker_cannot_abandon_an_accepted_transport_command():
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(_click):
        entered.set()
        await release.wait()

    manager, run, _, leader, _ = prepare(dispatch)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        # Force service-task cancellation at the owning worker boundary. The
        # public Stop only cancels its own waiter, tested separately above.
        worker = next(iter(run._workers))
        worker.cancel()
        assert not await run.stop(timeout=0.01)
        worker.cancel()
        assert not await run.stop(timeout=0.01)
        assert manager.action_sync.for_browser(leader) is run
    finally:
        release.set()
        assert await run.stop(timeout=1)
    assert run.uncertain


@pytest.mark.asyncio
async def test_close_timeout_keeps_lease_until_a_later_close_can_confirm_drain(monkeypatch):
    from camoufox_pm.core import browser_session

    monkeypatch.setattr(browser_session, "ACTION_SYNC_DRAIN_SECONDS", 0.02)
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(_click):
        entered.set()
        await release.wait()

    manager, run, _, _, (follower,) = prepare(dispatch)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(browser_session.BrowserTerminationError):
            await manager.close_and_release(follower.profile_id)
        assert manager.active_sessions[follower.profile_id] is follower
        manager._storage.release_lease.assert_not_awaited()
    finally:
        release.set()
        assert await run.stop(timeout=1)
    assert await manager.close_and_release(follower.profile_id)
    assert follower.profile_id not in manager.active_sessions
    manager._storage.release_lease.assert_awaited_once_with(follower.profile_id, "owner")


@pytest.mark.asyncio
async def test_stop_waits_for_startup_authorization_before_releasing_admission():
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(_target):
        entered.set()
        await release.wait()
        return True

    dispatch = AsyncMock()
    manager, run, _, leader, _ = prepare(dispatch, authorize=authorize)
    starting = asyncio.create_task(run.start())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not await run.stop(timeout=0.01)
        assert manager.action_sync.for_browser(leader) is run
    finally:
        release.set()
        assert not await starting
        assert await run.stop(timeout=1)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_all_closes_follower_transport_before_waiting_for_cohort(monkeypatch):
    from camoufox_pm.core import browser_session

    monkeypatch.setattr(browser_session, "ACTION_SYNC_DRAIN_SECONDS", 0.05)
    entered, transport_closed = asyncio.Event(), asyncio.Event()

    async def dispatch(_click):
        entered.set()
        await transport_closed.wait()

    class FollowerTransport:
        async def __aexit__(self, *_):
            transport_closed.set()

    manager, run, _, leader, (follower,) = prepare(dispatch)
    follower.camoufox = FollowerTransport()
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert await manager.close_all() == 2
        assert not manager.active_sessions
        assert {c.args for c in manager._storage.release_lease.await_args_list} == {
            (leader.profile_id, "owner"),
            (follower.profile_id, "owner"),
        }
    finally:
        transport_closed.set()
        assert await run.stop(timeout=1)


@pytest.mark.asyncio
async def test_leader_disconnection_fences_playback_in_the_close_callback():
    entered, release = asyncio.Event(), asyncio.Event()
    checks = 0

    async def authorize(_target):
        nonlocal checks
        checks += 1
        if checks == 2:
            entered.set()
            await release.wait()
        return True

    class Context:
        def on(self, _event, close):
            self.close = close

    dispatch = AsyncMock()
    manager, run, _, leader, _ = prepare(dispatch, authorize=authorize)
    context = Context()
    # Bind the manager's real close callback as launch would.
    manager._register_close_handler(context, leader)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        release.set()
        context.close()
        assert run.draining.is_set()
        await asyncio.wait_for(run.stopped.wait(), 1)
        dispatch.assert_not_awaited()
    finally:
        release.set()
        assert await run.stop(timeout=1)
        await asyncio.gather(*manager._exit_tasks)


@pytest.mark.asyncio
async def test_stop_during_adapter_preflight_prevents_the_transport_send():
    entered, ready = asyncio.Event(), asyncio.Event()
    send = AsyncMock()

    async def preflight(_click):
        entered.set()
        await ready.wait()
        return send

    _, run, _, _, _ = prepare(preflight, use_preflight=True)
    try:
        assert await run.start()
        assert submit(run, 1)
        await asyncio.wait_for(entered.wait(), 1)
        run.stop_accepting()
        ready.set()
        assert await run.stop(timeout=1)
        send.assert_not_called()
        assert run.reason == "requested"
    finally:
        ready.set()
        assert await run.stop(timeout=1)


@pytest.mark.asyncio
async def test_cancelling_start_at_authorization_completion_does_not_arm_capture():
    starting = None

    async def authorize(sessions):
        assert [session.profile_id for session in sessions] == ["leader", "follower-0"]
        assert starting is not None
        operation = asyncio.current_task()
        operation.add_done_callback(lambda _: starting.cancel())
        return True

    dispatch = AsyncMock()
    _, run, _, _, _ = prepare(dispatch, authorize=authorize)
    starting = asyncio.create_task(run.start())
    try:
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert not run.running
        assert not submit(run, 1)
        dispatch.assert_not_awaited()
    finally:
        assert await run.stop(timeout=1)
