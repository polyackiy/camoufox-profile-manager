"""Operator execution of explicit signed retention decisions; metadata survives."""

from __future__ import annotations

import time
from pathlib import Path

import psutil

from .identity import digest
from .protocol import parse_manifest
from .retention import SignedRetention, verify_retention


def collect_retained_ciphertext(directory: Path, *, limit: int = 100) -> dict[str, int]:
    from .relay import _Store

    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Invalid maintenance limit")
    try:
        if not (directory / "relay.sqlite3").is_file():
            raise ValueError
        store = _Store(directory)
        selected = []
        skipped = failed = collected = 0
        with store.transaction() as db:
            now = int(time.time())
            highwater = db.execute("SELECT highwater FROM clock WHERE id=1").fetchone()[0]
            rollback = now < highwater
            if not rollback:
                db.execute("UPDATE clock SET highwater=? WHERE id=1", (now,))
            rows = db.execute(
                "SELECT r.owner,r.id,r.state,r.manifest,i.decision,d.document FROM revisions r JOIN retention_items i ON i.owner=r.owner AND i.revision=r.id JOIN retention_decisions d ON d.owner=i.owner AND d.id=i.decision WHERE d.state IN ('scheduled','collecting') AND (r.state='collecting' OR (r.state='committed' AND d.eligible_at<=? AND ?=0)) ORDER BY r.state, d.eligible_at,r.id LIMIT ?",
                (now, int(rollback), limit),
            ).fetchall()
            for owner, revision, state, manifest_text, decision_id, document in rows:
                root, _ = store.owner(db, owner)
                decision = SignedRetention.model_validate_json(document)
                verify_retention(root, decision)
                manifest = parse_manifest(manifest_text.encode())
                item = next(
                    (item for item in decision.body.revisions if item.revision_id == revision), None
                )
                if (
                    item is None
                    or item.profile_id != manifest.profile_id
                    or item.manifest_sha256 != digest(manifest)
                    or decision.body.decision_id != decision_id
                ):
                    raise ValueError
                if state != "collecting" and (rollback or decision.body.not_before > now):
                    skipped += 1
                    continue
                if db.execute(
                    "SELECT 1 FROM profiles WHERE owner=? AND head=?", (owner, revision)
                ).fetchone():
                    skipped += 1
                    continue
                live = False
                for lease, pid, created in db.execute(
                    "SELECT id,pid,created FROM active_downloads WHERE owner=? AND revision=?",
                    (owner, revision),
                ).fetchall():
                    try:
                        if abs(psutil.Process(pid).create_time() - created) < 0.01:
                            live = True
                            continue
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.Error, OSError):
                        live = True
                        continue
                    db.execute("DELETE FROM active_downloads WHERE id=?", (lease,))
                if live:
                    skipped += 1
                    continue
                db.execute(
                    "UPDATE revisions SET state='collecting' WHERE owner=? AND id=?",
                    (owner, revision),
                )
                db.execute(
                    "UPDATE retention_decisions SET state='collecting' WHERE owner=? AND id=?",
                    (owner, decision_id),
                )
                selected.append((owner, revision, decision_id))
        # The durable collecting fence prevents new downloads/commit and makes
        # cancellation impossible before any filesystem mutation occurs.
        for owner, revision, decision_id in selected:
            with store.transaction() as db:
                row = db.execute(
                    "SELECT state FROM revisions WHERE owner=? AND id=?", (owner, revision)
                ).fetchone()
                if row != ("collecting",):
                    continue
                if db.execute(
                    "SELECT 1 FROM profiles WHERE owner=? AND head=?", (owner, revision)
                ).fetchone():
                    skipped += 1
                    continue
                try:
                    store.blob_path(owner, revision).unlink(missing_ok=True)
                    store.sync_directory(store.blobs)
                except Exception:
                    failed += 1
                    continue
                db.execute(
                    "UPDATE revisions SET state='collected',size=0 WHERE owner=? AND id=?",
                    (owner, revision),
                )
                if not db.execute(
                    "SELECT 1 FROM retention_items i JOIN revisions r ON r.owner=i.owner AND r.id=i.revision WHERE i.owner=? AND i.decision=? AND r.state!='collected'",
                    (owner, decision_id),
                ).fetchone():
                    db.execute(
                        "UPDATE retention_decisions SET state='collected' WHERE owner=? AND id=?",
                        (owner, decision_id),
                    )
                collected += 1
        return {"examined": len(rows), "collected": collected, "skipped": skipped, "failed": failed}
    except Exception:
        raise ValueError("Authorized retention maintenance failed") from None
