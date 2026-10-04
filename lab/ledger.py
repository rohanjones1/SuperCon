"""lab/ledger.py — append-only SQLite research ledger (stdlib only).

All writes go through the helper functions; UPDATE/DELETE are rejected by
triggers on every table. Timestamps are UTC ISO-8601. Foreign keys enabled.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("data/ledger.sqlite")

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS prereg (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content   TEXT    NOT NULL,
    hash      TEXT    NOT NULL,
    timestamp TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id      INTEGER,
    hypothesis_id  TEXT,
    structure_hash TEXT,
    formula        TEXT NOT NULL,
    generator      TEXT,
    novelty_flag   INTEGER DEFAULT 0,
    batch_id       TEXT,
    timestamp      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id    INTEGER,
    tool             TEXT NOT NULL,
    tool_version     TEXT NOT NULL,
    model_checkpoint TEXT,
    seed             INTEGER,
    hardware         TEXT,
    wall_ms          REAL,
    timestamp        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    candidate_id INTEGER REFERENCES candidates(id),
    tier         INTEGER NOT NULL,
    metric       TEXT    NOT NULL,
    value        REAL    NOT NULL,
    uncertainty  REAL,
    timestamp    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS controls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    control_id TEXT    NOT NULL,
    expected   TEXT    NOT NULL,
    observed   TEXT    NOT NULL,
    passed     INTEGER NOT NULL,
    timestamp  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS timing (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    start TEXT NOT NULL,
    end   TEXT NOT NULL
);
"""

_APPEND_ONLY_TABLES = ["prereg", "candidates", "runs", "results", "controls", "timing"]


def _triggers_for(table: str) -> str:
    return f"""
CREATE TRIGGER IF NOT EXISTS no_update_{table}
    BEFORE UPDATE ON {table}
BEGIN
    SELECT RAISE(ABORT, 'UPDATE not allowed on append-only table {table}');
END;

CREATE TRIGGER IF NOT EXISTS no_delete_{table}
    BEFORE DELETE ON {table}
BEGIN
    SELECT RAISE(ABORT, 'DELETE not allowed on append-only table {table}');
END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def init_db(path: Path = DEFAULT_PATH) -> None:
    """Create tables and append-only triggers. Safe to call repeatedly (IF NOT EXISTS)."""
    con = _connect(path)
    con.executescript(_DDL)
    for table in _APPEND_ONLY_TABLES:
        con.executescript(_triggers_for(table))
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Canonical JSON hash (stable key order, no whitespace)
# ---------------------------------------------------------------------------

def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Write helpers (append-only; no update/delete exposed)
# ---------------------------------------------------------------------------

def record_prereg(content: dict, path: Path = DEFAULT_PATH) -> str:
    """Insert a pre-registration record; return its SHA-256 hex digest."""
    canon = _canonical(content)
    h = hashlib.sha256(canon.encode()).hexdigest()
    con = _connect(path)
    con.execute(
        "INSERT INTO prereg (content, hash, timestamp) VALUES (?, ?, ?)",
        (canon, h, _now()),
    )
    con.commit()
    con.close()
    return h


def record_run(
    tool: str,
    tool_version: str,
    model_checkpoint: str | None,
    seed: int | None,
    hardware: str | None,
    wall_ms: float | None,
    path: Path = DEFAULT_PATH,
    experiment_id: int | None = None,
) -> int:
    """Insert a run record and return the new run_id (int).

    ``experiment_id`` optionally links the run to a prereg row (existing column).
    """
    con = _connect(path)
    cur = con.execute(
        """INSERT INTO runs
           (experiment_id, tool, tool_version, model_checkpoint, seed, hardware, wall_ms, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (experiment_id, tool, tool_version, model_checkpoint, seed, hardware, wall_ms, _now()),
    )
    run_id = cur.lastrowid
    con.commit()
    con.close()
    return run_id


def record_result(
    run_id: int,
    candidate_id: int | None,
    tier: int,
    metric: str,
    value: float,
    uncertainty: float | None = None,
    path: Path = DEFAULT_PATH,
) -> int:
    """Insert a result row and return its id."""
    con = _connect(path)
    cur = con.execute(
        """INSERT INTO results
           (run_id, candidate_id, tier, metric, value, uncertainty, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, candidate_id, tier, metric, value, uncertainty, _now()),
    )
    result_id = cur.lastrowid
    con.commit()
    con.close()
    return result_id


def record_control(
    run_id: int,
    control_id: str,
    expected: str,
    observed: str,
    passed: bool,
    path: Path = DEFAULT_PATH,
) -> int:
    """Insert a control record and return its id."""
    con = _connect(path)
    cur = con.execute(
        """INSERT INTO controls
           (run_id, control_id, expected, observed, passed, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, control_id, expected, observed, int(passed), _now()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def record_candidate(
    formula: str,
    hypothesis_id: str | None = None,
    batch_id: str | None = None,
    generator: str | None = None,
    path: Path = DEFAULT_PATH,
) -> int:
    """Insert a candidate row (existing schema) and return its id."""
    con = _connect(path)
    cur = con.execute(
        """INSERT INTO candidates (formula, hypothesis_id, batch_id, generator, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (formula, hypothesis_id, batch_id, generator, _now()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def record_timing(stage: str, start: str, end: str, path: Path = DEFAULT_PATH) -> None:
    """Insert a timing record."""
    con = _connect(path)
    con.execute(
        "INSERT INTO timing (stage, start, end) VALUES (?, ?, ?)",
        (stage, start, end),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Read helper
# ---------------------------------------------------------------------------

def get_run(run_id: int, path: Path = DEFAULT_PATH) -> dict:
    """Return run row as a plain dict. Raises KeyError if run_id not found."""
    con = _connect(path)
    row = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    con.close()
    if row is None:
        raise KeyError(f"run_id {run_id} not found")
    return dict(row)


def get_prereg(prereg_id: int, path: Path = DEFAULT_PATH) -> dict:
    """Return prereg row with ``content`` parsed from JSON. Raises KeyError if not found."""
    con = _connect(path)
    row = con.execute("SELECT * FROM prereg WHERE id = ?", (prereg_id,)).fetchone()
    con.close()
    if row is None:
        raise KeyError(f"prereg id {prereg_id} not found")
    out = dict(row)
    out["content"] = json.loads(out["content"])
    return out


def get_prereg_id(hash_hex: str, path: Path = DEFAULT_PATH) -> int:
    """Return the id of the most recent prereg row with this hash. Raises KeyError if absent."""
    con = _connect(path)
    row = con.execute(
        "SELECT id FROM prereg WHERE hash = ? ORDER BY id DESC LIMIT 1", (hash_hex,)
    ).fetchone()
    con.close()
    if row is None:
        raise KeyError(f"prereg hash {hash_hex} not found")
    return int(row["id"])


def get_controls(run_id: int, path: Path = DEFAULT_PATH) -> list[dict]:
    """Return all control rows for *run_id* in insertion order."""
    con = _connect(path)
    rows = con.execute(
        "SELECT * FROM controls WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]
