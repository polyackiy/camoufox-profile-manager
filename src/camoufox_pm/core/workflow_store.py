"""Workflow definitions and sanitized receipts in the installation database."""

import json
from datetime import datetime
from uuid import uuid4


class WorkflowConflict(ValueError):
    pass


class WorkflowStore:
    def __init__(self, storage):
        self.storage = storage

    @property
    def db(self):
        return self.storage.db._connection

    def list(self):
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT body FROM workflows ORDER BY rowid DESC")
        ]

    def get(self, workflow_id):
        row = self.db.execute("SELECT body FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, definition, owner, expected=None):
        from .workflow_models import planned_after

        prior = self.get(expected[0]) if expected else None
        if expected and (not prior or prior["revision"] != expected[1]):
            raise WorkflowConflict("Workflow changed. Reload before saving or running.")
        value = {
            "id": prior["id"] if prior else str(uuid4()),
            "revision": prior["revision"] + 1 if prior else 1,
            "owner": owner,
            "definition": definition.model_dump(),
            "next_run_at": planned_after(definition, datetime.now()),
        }
        with self.db:
            if expected:
                changed = self.db.execute(
                    "UPDATE workflows SET body=?,revision=? WHERE id=? AND revision=?",
                    (json.dumps(value), value["revision"], value["id"], expected[1]),
                ).rowcount
                if not changed:
                    raise WorkflowConflict("Workflow changed. Reload before saving or running.")
            else:
                self.db.execute(
                    "INSERT INTO workflows(id,revision,body) VALUES(?,?,?)",
                    (value["id"], value["revision"], json.dumps(value)),
                )
        return value

    def delete(self, workflow_id, revision):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute(
                "SELECT 1 FROM workflow_runs WHERE workflow_id=? AND active=1", (workflow_id,)
            ).fetchone():
                raise WorkflowConflict("Cancel the active run before deleting this workflow.")
            if not self.db.execute(
                "DELETE FROM workflows WHERE id=? AND revision=?", (workflow_id, revision)
            ).rowcount:
                raise WorkflowConflict("Workflow changed. Reload before saving or running.")

    def advance(self, value, next_run_at):
        value = {**value, "next_run_at": next_run_at}
        with self.db:
            self.db.execute(
                "UPDATE workflows SET body=? WHERE id=? AND revision=?",
                (json.dumps(value), value["id"], value["revision"]),
            )

    def runs(self, workflow_id):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT body FROM workflow_runs WHERE workflow_id=? ORDER BY rowid DESC LIMIT 30",
                (workflow_id,),
            )
        ]

    def run(self, run_id):
        row = self.db.execute("SELECT body FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def insert_run(self, value):
        import sqlite3

        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                definition = self.get(value["workflow_id"])
                if not definition or definition["revision"] != value["revision"]:
                    raise WorkflowConflict("Workflow changed. Reload before saving or running.")
                self.db.execute(
                    "INSERT INTO workflow_receipts(id,workflow_id) VALUES(?,?)",
                    (value["id"], value["workflow_id"]),
                )
                self.db.execute(
                    "INSERT INTO workflow_runs(id,workflow_id,active,body) VALUES(?,?,?,?)",
                    (
                        value["id"],
                        value["workflow_id"],
                        int(value["state"] in {"queued", "running"}),
                        json.dumps(value),
                    ),
                )
        except sqlite3.IntegrityError:
            raise WorkflowConflict(
                "A run is already active. Review it before starting another."
            ) from None

    def seen(self, run_id):
        return (
            self.db.execute("SELECT 1 FROM workflow_receipts WHERE id=?", (run_id,)).fetchone()
            is not None
        )

    def save_run(self, value):
        active = value["state"] in {"queued", "running"}
        with self.db:
            self.db.execute(
                "UPDATE workflow_runs SET active=?,body=? WHERE id=?",
                (int(active), json.dumps(value), value["id"]),
            )
            # Never prune active work. Bound retained, finished receipts per definition.
            self.db.execute(
                "DELETE FROM workflow_runs WHERE workflow_id=? AND active=0 AND id NOT IN (SELECT id FROM workflow_runs WHERE workflow_id=? ORDER BY rowid DESC LIMIT 30)",
                (value["workflow_id"], value["workflow_id"]),
            )

    def interrupted(self):
        for row in self.db.execute("SELECT body FROM workflow_runs WHERE active=1").fetchall():
            value = json.loads(row[0])
            value["state"] = "interrupted"
            value["finished_at"] = datetime.now().isoformat()
            for target in value["targets"]:
                if target["state"] in {"queued", "running"}:
                    target["state"] = "interrupted"
                    for step in target["steps"]:
                        if step["state"] in {"queued", "running"}:
                            step["state"] = "interrupted"
            self.save_run(value)
