from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonpatch


class RequestConflict(ValueError):
    pass


class Store:
    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        self.path = path
        self.readonly = readonly
        if readonly:
            if not path.is_file():
                raise FileNotFoundError(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL,
                  actor TEXT, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                  actor TEXT, id TEXT, input TEXT NOT NULL, result TEXT NOT NULL,
                  PRIMARY KEY(actor,id));
                CREATE TABLE IF NOT EXISTS controls (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, target TEXT,
                  consumed INTEGER DEFAULT 0);
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = (
            sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            if self.readonly
            else sqlite3.connect(self.path, timeout=30)
        )
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, state: dict) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO state VALUES (1,?)", (json.dumps(state),))
            self.append(connection, "controller", "initialized", {"state": state})

    def state(self) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
            return json.loads(row["body"])

    def event(self, actor: str | None, kind: str, body: dict) -> None:
        with self.connect() as connection:
            self.append(connection, actor, kind, body)

    def outcome(self, actor: str, key: str, action: str, payload: dict) -> dict | None:
        signature = json.dumps({"action": action, "payload": payload}, sort_keys=True)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT input,result FROM requests WHERE actor=? AND id=?", (actor, key)
            ).fetchone()
        if row is None:
            return None
        if row["input"] != signature:
            raise RequestConflict("idempotency_key_reused")
        return json.loads(row["result"])

    def receipt(self, actor: str, key: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT input,result FROM requests WHERE actor=? AND id=?", (actor, key)
            ).fetchone()
        return (
            {
                "found": True,
                "request": json.loads(row["input"]),
                "result": json.loads(row["result"]),
            }
            if row
            else {"found": False}
        )

    @staticmethod
    def append(connection: sqlite3.Connection, actor: str | None, kind: str, body: dict) -> None:
        connection.execute(
            "INSERT INTO events(time,actor,kind,body) VALUES (?,?,?,?)",
            (time.time(), actor, kind, json.dumps(body)),
        )

    def transact(
        self,
        actor: str,
        request_id: str,
        action: str,
        payload: dict,
        operation: Callable[[dict], tuple[dict, bool]],
    ) -> dict:
        signature = json.dumps({"action": action, "payload": payload}, sort_keys=True)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT input,result FROM requests WHERE actor=? AND id=?", (actor, request_id)
            ).fetchone()
            if previous:
                if previous["input"] != signature:
                    return {"ok": False, "reason": "idempotency_key_reused"}
                return json.loads(previous["result"])
            state = json.loads(
                connection.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
            )
            previous_state = deepcopy(state)
            result, changed = operation(state)
            if changed:
                state["version"] += 1
                recipient = payload.get("to") if action == "message" else None
                if recipient is not None:
                    if recipient != actor:
                        state.setdefault("inbox_activity", {})[recipient] = state["version"]
                else:
                    state.setdefault("activity", {})[actor] = state["version"]
                connection.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))
            patch = jsonpatch.make_patch(previous_state, state).patch if changed else []
            self.append(
                connection,
                actor,
                action,
                {
                    "payload": payload,
                    "result": result,
                    "patch": patch,
                },
            )
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,?)",
                (actor, request_id, signature, json.dumps(result)),
            )
            return result

    def update(self, operation: Callable[[dict], None]) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = json.loads(
                connection.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
            )
            previous_state = deepcopy(state)
            operation(state)
            connection.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))
            self.append(
                connection,
                "controller",
                "state_update",
                {
                    "patch": jsonpatch.make_patch(previous_state, state).patch,
                },
            )

    def events(self, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)
            ).fetchall()
            return [{**dict(row), "body": json.loads(row["body"])} for row in rows]

    def control(self, kind: str, target: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO controls(kind,target) VALUES (?,?)", (kind, target))

    def controls(self) -> list[dict]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM controls WHERE consumed=0 ORDER BY id"
            ).fetchall()
            connection.execute("UPDATE controls SET consumed=1 WHERE consumed=0")
            return [dict(row) for row in rows]
