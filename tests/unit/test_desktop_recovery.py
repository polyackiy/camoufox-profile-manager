from unittest.mock import Mock

import pytest

from camoufox_pm.desktop_recovery import RecoveryBridge


def test_recovery_rejects_stale_confirmation_and_never_dispatches():
    dispatch = Mock()
    bridge = RecoveryBridge(
        lambda: {"transaction_id": "one", "can_rollback": True, "checkpoint_timestamp": "now"},
        dispatch,
    )
    for transaction, timestamp in [("other", "now"), ("one", "old"), ("one", None)]:
        with pytest.raises(ValueError):
            bridge.action("rollback", transaction, timestamp)
    dispatch.assert_not_called()


def test_recovery_dispatches_exact_timestamp_and_closes_only_after_success():
    dispatch = Mock(side_effect=[RuntimeError("locked"), {"state": "queued"}])
    bridge = RecoveryBridge(
        lambda: {"transaction_id": "one", "can_rollback": True, "checkpoint_timestamp": "now"},
        dispatch,
    )
    bridge._window = Mock()
    with pytest.raises(RuntimeError):
        bridge.action("rollback", "one", "now")
    bridge._window.destroy.assert_not_called()
    assert bridge.action("rollback", "one", "now") == {"state": "queued"}
    dispatch.assert_called_with("rollback", "one", "now")
    bridge._window.destroy.assert_called_once()


@pytest.mark.parametrize(
    "status", [{"maintenance_active": True}, {"request_pending": True}, {"state": "validating"}]
)
def test_recovery_cannot_open_manager_before_safe_state(status):
    bridge = RecoveryBridge(lambda: status, Mock())
    with pytest.raises(ValueError):
        bridge.open_manager()
    assert not bridge._open_requested


def test_recovery_rechecks_capability_and_rejects_arbitrary_actions():
    dispatch = Mock()
    bridge = RecoveryBridge(lambda: {"can_resume": False}, dispatch)
    for action in ["resume", "apply", "/bin/sh"]:
        with pytest.raises(ValueError):
            bridge.action(action, "")
    dispatch.assert_not_called()


def test_unknown_status_cannot_open_manager():
    bridge = RecoveryBridge(lambda: {"state": "unknown_future_state"}, lambda *args: {})
    with pytest.raises(ValueError, match="Finish recovery"):
        bridge.open_manager()


def test_rollback_uses_retained_checkpoint_id_while_next_update_staged():
    dispatch = Mock(return_value={"state": "queued"})
    bridge = RecoveryBridge(
        lambda: {
            "transaction_id": "next-stage",
            "rollback_transaction_id": "accepted-update",
            "can_rollback": True,
            "checkpoint_timestamp": "now",
        },
        dispatch,
    )
    with pytest.raises(ValueError, match="transaction changed"):
        bridge.action("rollback", "next-stage", "now")
    bridge.action("rollback", "accepted-update", "now")
    dispatch.assert_called_once_with("rollback", "accepted-update", "now")
