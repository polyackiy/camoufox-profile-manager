"""Real TUF -> extraction -> runtime handoff -> actual child -> pair rollback."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from camoufox_pm.core import update_recovery
from camoufox_pm.core import update_runtime as runtime
from camoufox_pm.core.database import StorageManager
from camoufox_pm.core.maintenance import MaintenanceGate
from camoufox_pm.core.update_permissions import protect_path
from camoufox_pm.core.update_publisher import PublisherPolicy
from camoufox_pm.core.update_service import UpdateService


def archive(files, *, inventory=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        if inventory:
            entries = [
                {
                    "path": name,
                    "kind": "file",
                    "mode": 0o755 if name == "manager" else 0o644,
                    "length": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in files.items()
            ]
            zipped.writestr("update-package.json", json.dumps({"protocol": 1, "entries": entries}))
        for name, data in files.items():
            zipped.writestr(("payload/" if inventory else "") + name, data)
    return output.getvalue()


def repository(tmp_path, monkeypatch, artifacts):
    tuf = pytest.importorskip("tuf.ngclient")
    from securesystemslib.signer import CryptoSigner
    from tuf.api.exceptions import DownloadHTTPError
    from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp
    from tuf.ngclient.fetcher import FetcherInterface

    signer = CryptoSigner.generate_ed25519()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    root = Metadata(Root(expires=future, consistent_snapshot=False))
    for role in ("root", "targets", "snapshot", "timestamp"):
        root.signed.add_key(signer.public_key, role)
    root.sign(signer)
    trusted = tmp_path / "root.json"
    trusted.write_bytes(root.to_bytes())
    protect_path(trusted)
    targets = {}
    files = {}
    for name, (data, manifest) in artifacts.items():
        target = TargetFile.from_data(name, data)
        target.unrecognized_fields["custom"] = {"camoufox_pm": manifest}
        targets[name] = target
        files["/targets/" + name] = data
    metadata = Metadata(Targets(expires=future, targets=targets))
    metadata.sign(signer)
    snapshot = Metadata(Snapshot(expires=future, meta={"targets.json": MetaFile(version=1)}))
    snapshot.sign(signer)
    timestamp = Metadata(Timestamp(expires=future, snapshot_meta=MetaFile(version=1)))
    timestamp.sign(signer)
    files.update(
        {
            "/metadata/targets.json": metadata.to_bytes(),
            "/metadata/snapshot.json": snapshot.to_bytes(),
            "/metadata/timestamp.json": timestamp.to_bytes(),
        }
    )

    class Local(FetcherInterface):
        def _fetch(self, url):
            if urlsplit(url).path not in files:
                raise DownloadHTTPError("missing fixture", 404)
            yield files[urlsplit(url).path]

    real = tuf.Updater
    monkeypatch.setattr(
        tuf, "Updater", lambda *args, **kwargs: real(*args, **{**kwargs, "fetcher": Local()})
    )
    return trusted


def test_authenticated_runtime_pair_apply_and_new_manager_rollback(tmp_path, monkeypatch):
    protect_path(tmp_path)
    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    (installation / "release").write_text("old manager")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / "profiles.db"

    async def init():
        storage = StorageManager(str(database))
        await storage.initialize()
        await storage.close()

    asyncio.run(init())
    profiles = data / "profiles"
    profiles.mkdir()
    (profiles / "identity").write_text("old identity")
    browser = data / "browser"
    browser.mkdir()
    (browser / "config.json").write_text('{"active_version":"old browser"}')
    srv = UpdateService(
        data / "update-control", MaintenanceGate(data / "update-control" / "maintenance.sqlite3")
    )
    monkeypatch.setattr(runtime, "service", lambda: srv)
    monkeypatch.setattr(
        update_recovery,
        "configured_layout",
        lambda **kwargs: (
            database,
            {"profiles": profiles, "browser": browser},
            ("profiles", "browser"),
            "key-reference",
        ),
    )
    monkeypatch.setattr(update_recovery, "_legacy_processes_stopped", lambda: True)
    monkeypatch.setattr(runtime, "verify_publisher", lambda *args: None)
    monkeypatch.setattr(runtime, "_target_platform", lambda: "windows")
    monkeypatch.setattr(runtime, "_architecture", lambda: "x86_64")
    monkeypatch.setattr(runtime.platform, "version", lambda: "14.0")
    monkeypatch.setattr(runtime, "__version__", "1.0")
    script = b"""#!/usr/bin/env python3
import json,sqlite3,sys
from contextlib import closing
from pathlib import Path
receipt=Path(sys.argv[2]); request=json.loads(receipt.read_text())
t=json.loads((receipt.parent/'transaction.json').read_text())
with closing(sqlite3.connect(t['db_path'])) as db, db:
    db.execute('PRAGMA user_version=1')
(Path(t['paths']['profiles'])/'identity').write_text('new identity')
(Path(t['paths']['browser'])/'config.json').write_text('{"active_version":"tested beta pair"}')
receipt.write_text(json.dumps({'validated':request['transaction_id']}))
"""
    pair = "156.0-beta.2"
    base = {
        "protocol": 1,
        "component": "manager",
        "version": "2.0",
        "platform": "windows",
        "architecture": "x86_64",
        "channel": "stable",
        "artifact": "manager.zip",
        "schema_min": 0,
        "schema_max": 1,
        "schema_result": 1,
        "recovery": "checkpoint-v1",
        "minimum_updater": "1.0",
        "browser_version": pair,
        "publisher_policy": "fixture",
        "product_id": "fixture.product",
        "minimum_os": "1.0",
    }
    browser_manifest = {**base, "component": "camoufox", "version": pair, "artifact": "browser.zip"}
    trusted = repository(
        tmp_path,
        monkeypatch,
        {
            "manager.zip": (archive({"manager": script}, inventory=True), base),
            "browser.zip": (
                archive(
                    {
                        "version.json": json.dumps(
                            {"version": "156.0", "build": "beta.2"}
                        ).encode(),
                        "camoufox.exe": b"authenticated fake browser",
                    }
                ),
                browser_manifest,
            ),
        },
    )
    config = {
        "installation": str(installation),
        "entrypoint": "manager",
        "trusted_root": str(trusted),
        "metadata_url": "https://fixture.test/metadata/",
        "targets_url": "https://fixture.test/targets/",
        "channels": {"stable": "manager.zip", "preview": "manager.zip"},
        "schema": 1,
        "updater_version": "1.0",
        "browser": {
            "trusted_root": str(trusted),
            "metadata_url": "https://fixture.test/metadata/",
            "targets_url": "https://fixture.test/targets/",
            "artifact_template": "browser.zip",
        },
    }
    # Temp deployment capability avoids touching any installed app. Native OS
    # publisher checks are covered separately; real TUF trust remains in force.
    monkeypatch.setattr(
        runtime, "deployment", lambda: (config, PublisherPolicy("fixture", "fixture.product"))
    )
    original = subprocess.run

    def launcher(command, *args, **kwargs):
        if os.name == "nt" and Path(command[0]).name == "manager":
            command = [sys.executable, *command]
        return original(command, *args, **kwargs)

    monkeypatch.setattr("camoufox_pm.core.update_transaction.subprocess.run", launcher)
    staged = runtime.download("stable")
    assert staged["state"] == "staged"
    runtime.request("apply", staged["transaction_id"])
    result = runtime.execute_request("apply")
    assert result["state"] == "committed"
    assert (profiles / "identity").read_text() == "new identity"
    assert list((browser / "browsers" / "cpm-verified").glob("*"))
    assert not srv.gate.blocked
    # Rollback is requested by the upgraded manager, using retained local TUF
    # metadata and exact timestamp, without matching the old source version.
    monkeypatch.setattr(runtime, "__version__", "2.0")

    # Accepted rollback must survive expired/unavailable update metadata. It
    # authenticates the retained original tree and publisher from local policy.
    def unavailable(*args, **kwargs):
        raise AssertionError("Rollback must not reauthenticate the newer target")

    monkeypatch.setattr(runtime, "_package", unavailable)
    monkeypatch.setattr(runtime, "_browser_package", unavailable)
    runtime.request("rollback", result["transaction_id"], result["checkpoint_timestamp"])
    assert (
        runtime.execute_request("rollback", result["checkpoint_timestamp"])["state"]
        == "rolled_back"
    )
    assert (installation / "release").read_text() == "old manager"
    assert (profiles / "identity").read_text() == "old identity"
    assert json.loads((browser / "config.json").read_text())["active_version"] == "old browser"
    assert not (browser / "browsers").exists()
    assert not srv.gate.blocked
