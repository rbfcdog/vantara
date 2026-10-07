import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .sources import SOURCE_FILES


def dataset_id(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for filename in SOURCE_FILES:
        data = files[filename]
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _connection():
    path = Path(os.environ.get("VANTARA_WORKFLOW_DB") or
                Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "vantara/workflow.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS cases (
        dataset TEXT NOT NULL, source TEXT NOT NULL, line INTEGER NOT NULL,
        status TEXT NOT NULL, owner TEXT NOT NULL, note TEXT NOT NULL,
        opened_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        version INTEGER NOT NULL, history TEXT NOT NULL,
        PRIMARY KEY (dataset, source, line)
    )""")
    return connection


def _now():
    return datetime.now(timezone.utc).isoformat()


def _case(row):
    return {key: json.loads(row[key]) if key == "history" else row[key]
            for key in ("source", "line", "status", "owner", "note", "opened_at", "updated_at", "version", "history")}


def _ensure(connection, dataset, source, line):
    at = _now()
    history = json.dumps([{"status": "open", "owner": "", "note": "", "at": at}], ensure_ascii=False)
    connection.execute("""INSERT OR IGNORE INTO cases
        (dataset, source, line, status, owner, note, opened_at, updated_at, version, history)
        VALUES (?, ?, ?, 'open', '', '', ?, ?, 0, ?)""",
                       (dataset, source, line, at, at, history))


def list_cases(dataset: str, identities: set[tuple[str, int]]) -> dict[str, dict]:
    with closing(_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        for source, line in identities:
            _ensure(connection, dataset, source, line)
        rows = connection.execute("SELECT * FROM cases WHERE dataset = ?", (dataset,)).fetchall()
    return {f"{row['source']}:{row['line']}": _case(row) for row in rows
            if (row["source"], row["line"]) in identities}


def update_case(dataset: str, source: str, line: int, status: str,
                owner: str, note: str, version: int) -> dict | None:
    with closing(_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure(connection, dataset, source, line)
        row = connection.execute("SELECT * FROM cases WHERE dataset = ? AND source = ? AND line = ?",
                                 (dataset, source, line)).fetchone()
        if row["version"] != version:
            return None
        if row["status"] == "closed" and status != "open":
            raise ValueError("Reabra o caso fechado antes de registrar outra decisão.")
        if row["status"] == "closed" and not note:
            raise ValueError("Registre o motivo para reabrir o caso fechado.")
        at = _now()
        history = json.loads(row["history"])
        history.append({"status": status, "owner": owner, "note": note, "at": at})
        connection.execute("""UPDATE cases SET status = ?, owner = ?, note = ?,
            updated_at = ?, version = ?, history = ? WHERE dataset = ? AND source = ? AND line = ?""",
                           (status, owner, note, at, version + 1, json.dumps(history, ensure_ascii=False),
                            dataset, source, line))
        updated = connection.execute("SELECT * FROM cases WHERE dataset = ? AND source = ? AND line = ?",
                                     (dataset, source, line)).fetchone()
    return _case(updated)
