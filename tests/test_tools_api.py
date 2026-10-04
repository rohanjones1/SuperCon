"""Tests for lab/tools_api.py — tmp CSV + tmp policy, no CHGNet, no network."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from lab import run_batch, tools_api

HEADER = (
    "material_id,formula,mp_energy_above_hull_summary,mp_energy_above_hull_gga,"
    "chgnet_hull_corrected,chgnet_hull_uncorrected,error\n"
)
ROWS = (
    "mp-1,AB,0.0,0.0,0.01,0.20,\n"         # RETAIN (corrected 0.01 <= cutoff)
    "mp-2,CD,0.30,0.30,0.20,0.01,\n"       # DEPRIORITIZE (corrected 0.20 > cutoff)
    "mp-3,EF,0.0,0.0,,,relax failed\n"     # excluded from calibration
)
POLICY = {
    "screening_cutoff_ev_per_atom": 0.0585322711221589,
    "ground_truth_threshold_ev_per_atom": 0.05,
    "frozen_convention": "corrected",
    "calibration_run_id": 3,
    "recall_at_cutoff": 0.8,
    "precision_at_cutoff": 0.75,
}


@pytest.fixture
def paths(tmp_path):
    csv_path = tmp_path / "cal.csv"
    csv_path.write_text(HEADER + ROWS, encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
    return csv_path, policy_path


def _call(ids, paths):
    return tools_api.lookup_screening(ids, csv_path=paths[0], policy_path=paths[1])


def test_found_candidates_fields_and_decisions(paths):
    out = _call(["mp-1", "mp-2"], paths)
    assert "error" not in out
    assert out["tier"] == "T1"
    assert out["frozen_convention"] == "corrected"
    assert out["ground_truth_criterion_ev_per_atom"] == 0.05
    assert out["caveat"] == tools_api.CAVEAT
    assert out["wall_ms"] >= 0
    r1, r2 = out["results"]
    assert r1["screening_decision"] == "RETAIN"
    assert r1["chgnet_hull_ev_per_atom"] == pytest.approx(0.01)  # corrected column, not uncorrected
    assert r2["screening_decision"] == "DEPRIORITIZE"
    for r in (r1, r2):
        assert r["in_sample"] is True
        assert r["source"] == "calibration_run_3"
        assert r["policy_calibration_run_id"] == 3
        assert r["screening_cutoff_ev_per_atom"] == POLICY["screening_cutoff_ev_per_atom"]
        assert "mp_dft_hull_ev_per_atom" not in r  # hidden by default
        assert "mp_dft_hull_label" not in r


def test_ground_truth_shown_when_requested(paths):
    out = tools_api.lookup_screening(
        ["mp-2"], csv_path=paths[0], policy_path=paths[1], include_ground_truth=True
    )
    r = out["results"][0]
    assert r["mp_dft_hull_ev_per_atom"] == pytest.approx(0.30)
    assert r["mp_dft_hull_label"] == tools_api.MP_DFT_HULL_LABEL


def test_notes_always_present(tmp_path, paths):
    outs = [
        _call(["mp-1"], paths),
        _call(["mp-999"], paths),
        _call([f"mp-{i}" for i in range(6)], paths),
        tools_api.lookup_screening(["mp-1"], csv_path=tmp_path / "missing.csv", policy_path=paths[1]),
    ]
    for out in outs:
        assert out["priority_note"] == tools_api.PRIORITY_NOTE
        assert out["timing_note"] == tools_api.TIMING_NOTE
        assert out["caveat"] == tools_api.CAVEAT


def test_cap_enforced(paths):
    out = _call([f"mp-{i}" for i in range(6)], paths)
    assert "error" in out
    assert out["caveat"] == tools_api.CAVEAT


def test_unknown_and_excluded_ids_not_in_cache(paths):
    out = _call(["mp-999", "mp-3"], paths)
    assert [r["status"] for r in out["results"]] == ["not_in_cache", "not_in_cache"]
    assert all(r["note"] == tools_api.NOT_IN_CACHE_NOTE for r in out["results"])
    assert out["caveat"] == tools_api.CAVEAT


def test_errors_returned_not_raised(tmp_path, paths):
    out = tools_api.lookup_screening(["mp-1"], csv_path=tmp_path / "missing.csv", policy_path=paths[1])
    assert "error" in out and out["caveat"] == tools_api.CAVEAT
    out = tools_api.lookup_screening("mp-1", csv_path=paths[0], policy_path=paths[1])  # not a list
    assert "error" in out
    bad = tmp_path / "bad_policy.json"
    bad.write_text(json.dumps({**POLICY, "ground_truth_threshold_ev_per_atom": 0.1}), encoding="utf-8")
    out = tools_api.lookup_screening(["mp-1"], csv_path=paths[0], policy_path=bad)
    assert "error" in out


def test_decision_comes_from_screen_batch(monkeypatch, paths):
    calls = []
    real = run_batch.screen_batch

    def spy(candidates, policy):
        calls.append([c.candidate_id for c in candidates])
        return [replace(c, screening_decision="SENTINEL") for c in real(candidates, policy)]

    monkeypatch.setattr(run_batch, "screen_batch", spy)
    out = _call(["mp-1", "mp-999"], paths)
    assert calls == [["mp-1"]]
    assert out["results"][0]["screening_decision"] == "SENTINEL"
    assert out["results"][1]["status"] == "not_in_cache"


def test_get_policy_has_caveat(paths):
    out = tools_api.get_policy(paths[1])
    assert out["screening_cutoff_ev_per_atom"] == POLICY["screening_cutoff_ev_per_atom"]
    assert out["caveat"] == tools_api.CAVEAT
    out = tools_api.get_policy(paths[1].parent / "nope.json")
    assert "error" in out and out["caveat"] == tools_api.CAVEAT
