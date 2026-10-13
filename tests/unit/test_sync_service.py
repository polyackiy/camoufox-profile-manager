from __future__ import annotations

from uuid import uuid4

import pytest

from camoufox_pm.sync.control import ControlConflict
from camoufox_pm.sync.identity import generate_device, pairing_fingerprint
from camoufox_pm.sync.service import EnrollmentError, SyncService


class MemoryVault:
    def __init__(self):
        self.data = {}
        self.writes = 0
        self.fail_write = None

    def put(self, slot, payload):
        self.writes += 1
        if self.writes == self.fail_write:
            raise ValueError("unavailable")
        self.data[slot] = payload

    def get(self, slot):
        return self.data.get(slot)

    def delete(self, slot):
        self.data.pop(slot, None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    svc = SyncService(tmp_path / "control")
    vault = MemoryVault()
    monkeypatch.setattr(svc, "_vault", lambda state, password: vault)
    return svc, vault


def ready(svc):
    svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    package, secret = svc.recovery_package("user-id")
    return svc.confirm_recovery("user-id", package, secret)


def test_bootstrap_recovery_is_required_and_confirmed_in_fresh_process(setup):
    svc, vault = setup
    assert svc.status("user-id") == {"phase": "not_enrolled"}
    status = svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    assert status["phase"] == "recovery_required"
    assert len(vault.data) == 2
    package, secret = svc.recovery_package("user-id")
    assert secret not in str(svc.control.get("installation").payload)
    assert svc.confirm_recovery("user-id", package, secret)["phase"] == "ready"
    with pytest.raises(EnrollmentError):
        svc.confirm_recovery("user-id", package, secret)
    with pytest.raises(EnrollmentError):
        svc.abandon_enrollment("user-id")


def test_partial_vault_write_stays_blocked_and_requires_explicit_abandon(setup):
    svc, vault = setup
    vault.fail_write = 2
    with pytest.raises(EnrollmentError):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    old_owner = svc.status("user-id")["owner_id"]
    assert svc.status("user-id")["phase"] == "preparing"
    with pytest.raises(EnrollmentError):
        svc.recovery_package("user-id")
    with pytest.raises(EnrollmentError):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    svc.abandon_enrollment("user-id")
    assert svc.control.get("abandoned/" + old_owner) is not None
    assert svc.status("user-id")["phase"] == "not_enrolled"
    assert (
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")["owner_id"]
        != old_owner
    )
    assert svc.control.get("abandoned/" + old_owner) is not None


def test_both_vault_writes_completed_but_journal_ack_failed_can_resume(setup, monkeypatch):
    svc, _ = setup
    original = svc.control.update

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(svc.control, "update", fail)
    with pytest.raises(OSError):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    assert svc.status("user-id")["phase"] == "preparing"
    monkeypatch.setattr(svc.control, "update", original)
    package, secret = svc.recovery_package("user-id")
    assert svc.confirm_recovery("user-id", package, secret)["phase"] == "ready"


def test_stable_local_user_binding_including_tombstones(setup):
    svc, _ = setup
    svc.begin_enrollment("original-user-id", "https://relay.example", "Laptop", "native")
    for action in (
        lambda: svc.status("new-user-same-name"),
        lambda: svc.recovery_package("new-user-same-name"),
        lambda: svc.abandon_enrollment("new-user-same-name"),
    ):
        with pytest.raises(EnrollmentError):
            action()
    svc.abandon_enrollment("original-user-id")
    with pytest.raises(EnrollmentError):
        svc.begin_enrollment("new-user-same-name", "https://relay.example", "Laptop", "native")


def test_malformed_or_signature_modified_control_fails_closed(setup):
    svc, _ = setup
    svc.control.create("installation", {"phase": "abandoned"})
    with pytest.raises(EnrollmentError):
        svc.status("user-id")
    with pytest.raises(EnrollmentError):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")


def test_status_reads_installation_once_and_rechecks_auth(setup, monkeypatch):
    svc, _ = setup
    ready(svc)
    get = svc.control.get
    reads = []

    def counted(key):
        reads.append(key)
        return get(key)

    monkeypatch.setattr(svc.control, "get", counted)
    auth = []
    assert svc.status("user-id", authorize=lambda: auth.append(True))["phase"] == "ready"
    assert reads == ["installation"]
    assert len(auth) == 2


def test_denied_authorization_never_commits_or_exports(setup):
    svc, vault = setup

    class Denied(Exception):
        pass

    def deny():
        raise Denied

    with pytest.raises(Denied):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native", authorize=deny)
    assert svc.control.get("installation") is None
    assert not vault.data
    ready(svc)
    with pytest.raises(Denied):
        svc.recovery_package("user-id", authorize=deny)
    with pytest.raises(Denied):
        svc.status("user-id", authorize=deny)


def test_concurrent_bootstrap_cannot_replace_reserved_owner(setup, monkeypatch):
    svc, _ = setup
    original_create = svc.control.create

    def competing(key, payload):
        original_create(key, payload)
        return original_create(key, payload)

    monkeypatch.setattr(svc.control, "create", competing)
    with pytest.raises(ControlConflict):
        svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    assert svc.status("user-id")["phase"] == "preparing"


def test_wrong_recovery_package_never_marks_ready(setup):
    svc, _ = setup
    svc.begin_enrollment("user-id", "https://relay.example", "Laptop", "native")
    with pytest.raises(ValueError):
        svc.confirm_recovery("user-id", b"invalid", "x" * 43)
    assert svc.status("user-id")["phase"] == "recovery_required"


def test_pairing_approval_consumes_invitation_and_advances_membership_atomically(setup):
    svc, _ = setup
    ready(svc)
    _, state = svc._read("user-id")
    keys = generate_device()
    certificate = keys.certificate(state.root.owner_id, str(uuid4()))
    request = svc.create_pairing_invitation("user-id", certificate, now=100)
    approval = svc.approve_pairing_invitation(
        "user-id", request, pairing_fingerprint(request), now=150
    )
    assert approval.membership.membership.epoch == 2
    assert svc.status("user-id")["membership_epoch"] == 2
    assert svc.control.get("invitation/" + request.invitation_id).payload["phase"] == "consumed"
    with pytest.raises(EnrollmentError):
        svc.approve_pairing_invitation("user-id", request, pairing_fingerprint(request), now=150)


def test_failed_pairing_transaction_does_not_consume_or_change_epoch(setup, monkeypatch):
    svc, _ = setup
    ready(svc)
    _, state = svc._read("user-id")
    request = svc.create_pairing_invitation(
        "user-id", generate_device().certificate(state.root.owner_id, str(uuid4())), now=100
    )

    def fail(*args, **kwargs):
        raise ControlConflict("race")

    monkeypatch.setattr(svc.control, "batch", fail)
    with pytest.raises(ControlConflict):
        svc.approve_pairing_invitation("user-id", request, pairing_fingerprint(request), now=150)
    assert svc.status("user-id")["membership_epoch"] == 1
    assert svc.control.get("invitation/" + request.invitation_id).payload["phase"] == "pending"


def test_expired_or_substituted_invitation_does_not_enroll(setup):
    svc, _ = setup
    ready(svc)
    _, state = svc._read("user-id")
    request = svc.create_pairing_invitation(
        "user-id", generate_device().certificate(state.root.owner_id, str(uuid4())), now=100
    )
    with pytest.raises(ValueError):
        svc.approve_pairing_invitation("user-id", request, pairing_fingerprint(request), now=400)
    with pytest.raises(ValueError):
        svc.approve_pairing_invitation("user-id", request, "0" * 64, now=150)
    changed = request.model_copy(update={"invitation_id": str(uuid4())})
    with pytest.raises(EnrollmentError):
        svc.approve_pairing_invitation("user-id", changed, pairing_fingerprint(changed), now=150)
    assert svc.status("user-id")["membership_epoch"] == 1


def test_recipient_is_one_use_device_only_and_freshness_unknown(setup, tmp_path, monkeypatch):
    sender, _ = setup
    ready(sender)
    _, state = sender._read("user-id")
    keys = generate_device()
    request = sender.create_pairing_invitation(
        "user-id", keys.certificate(state.root.owner_id, str(uuid4())), now=100
    )
    approval = sender.approve_pairing_invitation(
        "user-id", request, pairing_fingerprint(request), now=150
    )
    receiver = SyncService(tmp_path / "receiver")
    vault = MemoryVault()
    monkeypatch.setattr(receiver, "_vault", lambda state, password: vault)

    def consume():
        return receiver.consume_pairing_approval(
            "receiver-user",
            state.root,
            request,
            approval,
            keys,
            "Desktop",
            "native",
            now=150,
            confirmed_fingerprint=pairing_fingerprint(request),
        )

    assert consume()["phase"] == "freshness_unknown"
    assert set(vault.data) == {request.device.device_id}
    assert not receiver.status("receiver-user")["authority"]
    with pytest.raises(EnrollmentError):
        consume()
    with pytest.raises(EnrollmentError):
        receiver.recovery_package("receiver-user")


def test_recipient_key_write_failure_resumes_exact_ceremony(setup, tmp_path, monkeypatch):
    sender, _ = setup
    ready(sender)
    _, state = sender._read("user-id")
    keys = generate_device()
    request = sender.create_pairing_invitation(
        "user-id", keys.certificate(state.root.owner_id, str(uuid4())), now=100
    )
    approval = sender.approve_pairing_invitation(
        "user-id", request, pairing_fingerprint(request), now=150
    )
    receiver = SyncService(tmp_path / "receiver")
    vault = MemoryVault()
    vault.fail_write = 1
    monkeypatch.setattr(receiver, "_vault", lambda state, password: vault)

    def consume():
        return receiver.consume_pairing_approval(
            "receiver-user",
            state.root,
            request,
            approval,
            keys,
            "Desktop",
            "native",
            now=150,
            confirmed_fingerprint=pairing_fingerprint(request),
        )

    with pytest.raises(ValueError):
        consume()
    assert receiver.status("receiver-user")["phase"] == "preparing"
    assert consume()["phase"] == "freshness_unknown"


def test_routine_device_unlock_never_reads_authority_slot(setup, monkeypatch):
    svc, vault = setup
    status = ready(svc)
    original_get = vault.get
    reads = []

    def device_only(slot):
        reads.append(slot)
        if slot == status["owner_id"]:
            pytest.fail("routine unlock must not read authority")
        return original_get(slot)

    monkeypatch.setattr(vault, "get", device_only)
    keys = svc.device_keys("user-id")
    assert (
        keys.certificate(status["owner_id"], status["device_id"]).device_id == status["device_id"]
    )
    assert reads == [status["device_id"]]
