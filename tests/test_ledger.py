"""tests/test_ledger.py — M1 ledger tests."""
from __future__ import annotations

import sqlite3

import pytest

from lab.ledger import (
    get_run,
    init_db,
    record_control,
    record_prereg,
    record_result,
    record_run,
    record_timing,
)


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "test_ledger.sqlite"
    init_db(p)
    return p


# (a) run round-trips with all fields
def test_run_roundtrip(db):
    run_id = record_run(
        tool="chgnet_relax",
        tool_version="0.3.8",
        model_checkpoint="v0.3.0",
        seed=42,
        hardware="CPU",
        wall_ms=1234.5,
        path=db,
    )
    row = get_run(run_id, path=db)
    assert row["tool"] == "chgnet_relax"
    assert row["tool_version"] == "0.3.8"
    assert row["model_checkpoint"] == "v0.3.0"
    assert row["seed"] == 42
    assert row["hardware"] == "CPU"
    assert row["wall_ms"] == pytest.approx(1234.5)
    assert row["timestamp"]


# (b) UPDATE is rejected
def test_update_rejected(db):
    run_id = record_run("tool", "1.0", None, None, None, None, path=db)
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError, match="UPDATE not allowed"):
        con.execute("UPDATE runs SET tool = 'hacked' WHERE id = ?", (run_id,))
    con.close()


# (b) DELETE is rejected
def test_delete_rejected(db):
    run_id = record_run("tool", "1.0", None, None, None, None, path=db)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError, match="DELETE not allowed"):
        con.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    con.close()


# (c) same prereg content always yields the same hash
def test_prereg_deterministic(db):
    content = {"question": "screen borides", "threshold": 0.05, "split": "family"}
    h1 = record_prereg(content, path=db)
    h2 = record_prereg(content, path=db)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digits


# key order must not matter for the hash
def test_prereg_key_order_independent(db):
    h1 = record_prereg({"a": 1, "b": 2}, path=db)
    h2 = record_prereg({"b": 2, "a": 1}, path=db)
    assert h1 == h2


# (d) result with a bad run_id is rejected by the FK
def test_result_bad_run_id_rejected(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO results (run_id, candidate_id, tier, metric, value, uncertainty, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (99999, None, 1, "e_hull", 0.01, None, "2026-01-01T00:00:00+00:00"),
        )
        con.commit()
    con.close()


# smoke: record_control and record_timing don't raise
def test_control_and_timing_smoke(db):
    run_id = record_run("tool", "1.0", None, None, None, None, path=db)
    cid = record_control(run_id, "pos_ctrl_MgB2", "stable", "stable", True, path=db)
    assert isinstance(cid, int)
    record_timing("relax", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", path=db)
