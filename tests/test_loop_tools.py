"""Tests for lab/loop_tools.py — one deterministic Hypothesis->Planner->Runner->Analyst iteration.

tmp reports, tmp ledger, tmp run dir. No CHGNet, no network, no Omnigent, no LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab import ledger, loop_tools as lt
from tests.test_test_menu import ARM_A, CAL

POLICY = {
    "screening_cutoff_ev_per_atom": 0.0585322711221589,
    "ground_truth_threshold_ev_per_atom": 0.05,
    "frozen_convention": "corrected",
    "calibration_run_id": 3,
    "recall_at_cutoff": 0.8, "precision_at_cutoff": 0.75,
    "recall_wilson_ci_95": [0.55, 0.93], "precision_wilson_ci_95": [0.51, 0.90],
    "recall_target": 0.8,
}
# a: RETAIN + MP-stable (TP); b: RETAIN + MP-unstable (FP); c, d: DEPRIORITIZE + unstable (TN); e: failed.
CSV = (
    "material_id,formula,mp_energy_above_hull_summary,chgnet_hull_corrected,chgnet_hull_uncorrected,wall_ms,error\n"
    "mp-a,AB,0.0,0.01,0.5,1000,\n"
    "mp-b,CD,0.10,0.02,0.5,1000,\n"
    "mp-c,EF,0.30,0.20,0.0,1000,\n"
    "mp-d,GH,0.40,0.30,0.0,1000,\n"
    "mp-e,IJ,0.0,,,1000,relax failed\n"
)
BUDGET = "budget_id: budget_test\ncandidate_pool_size: 200\ntotal_compute_budget_s: 900\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "arm_a_results_run1.json").write_text(json.dumps(ARM_A), encoding="utf-8")
    (rdir / "stability_calibration.json").write_text(json.dumps(CAL), encoding="utf-8")
    (rdir / "stability_policy.json").write_text(json.dumps(POLICY), encoding="utf-8")
    (rdir / "stability_calibration.csv").write_text(CSV, encoding="utf-8")
    (tmp_path / "budget.yaml").write_text(BUDGET, encoding="utf-8")
    monkeypatch.setattr(lt, "LEDGER_PATH", tmp_path / "ledger.sqlite")
    monkeypatch.setattr(lt, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(lt, "REPORTS_DIR", rdir)
    monkeypatch.setattr(lt, "CSV_PATH", rdir / "stability_calibration.csv")
    monkeypatch.setattr(lt, "POLICY_PATH", rdir / "stability_policy.json")
    monkeypatch.setattr(lt, "BUDGET_PATH", tmp_path / "budget.yaml")
    return tmp_path


def _hyp():
    return lt.register_hypothesis("[test] gate shrinks pool, keeps MP-stable", "substantial", "high")


def _spec(hid, test="chgnet_triage"):
    return lt.register_experiment_spec(hid, test, "alt", "learning", "stop rule", "unknowns")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_full_handoff_deterministic(env):
    pool = lt.get_candidate_pool()
    assert pool["n_candidates"] == 4  # failed row excluded
    assert not any(k.startswith("mp_") for k in _keys(pool))

    hyp = _hyp()
    assert isinstance(hyp["hypothesis_id"], int) and hyp["label"] == "agent_generated"

    spec = _spec(hyp["hypothesis_id"])
    assert "error" not in spec and spec["chosen_test"] == "chgnet_triage"
    stored = ledger.get_prereg(spec["spec_id"], path=lt.LEDGER_PATH)["content"]
    assert stored["hypothesis_id"] == hyp["hypothesis_id"]  # Planner received the hypothesis

    run = lt.run_stability_screen(spec["spec_id"])
    assert run["status"] == "valid" and run["spec_id"] == spec["spec_id"]  # Runner received the spec
    assert (run["n_screened"], run["n_retained"], run["n_deprioritized"]) == (4, 2, 2)
    assert run["validation_queue_top5"] == ["mp-a", "mp-b"]
    assert ledger.get_run(run["run_id"], path=lt.LEDGER_PATH)["experiment_id"] == spec["spec_id"]

    ev = lt.evaluate_iteration(run["run_id"])  # Analyst received the Runner result
    assert ev["verdict"] == "supported"
    assert ev["observed"] == {"pool_reduction": "substantial", "recall_of_mp_stable": "high"}
    assert ev["retrospective_mp_metrics"]["tp"] == 1 and ev["retrospective_mp_metrics"]["fp"] == 1
    assert ev["deterministic_decision_signal"] == "narrow_downstream_to_survivors"

    dec = lt.record_next_decision(run["run_id"], ev["deterministic_decision_signal"], "learned", "why")
    assert dec["next_decision"] == "narrow_downstream_to_survivors"
    assert dec["matches_deterministic_signal"] is True
    md = (lt.REPORTS_DIR / f"iteration_run{run['run_id']}.md").read_text(encoding="utf-8")
    assert "Next decision" in md and "RETAINED BY CALIBRATED SCREENING" in md
    assert "CHGNet stable" not in md and "in-sample" in md


class _GuardedRow(dict):
    def __getitem__(self, k):
        if k.startswith("mp_"):
            raise AssertionError(f"Runner read ground-truth column {k}")
        return super().__getitem__(k)

    def get(self, k, default=None):
        if k.startswith("mp_"):
            raise AssertionError(f"Runner read ground-truth column {k}")
        return super().get(k, default)


def test_runner_blind_to_ground_truth(env, monkeypatch):
    spec = _spec(_hyp()["hypothesis_id"])
    real = lt._pool_rows
    monkeypatch.setattr(lt, "_pool_rows", lambda: {m: _GuardedRow(r) for m, r in real().items()})
    run = lt.run_stability_screen(spec["spec_id"])
    assert "error" not in run, run
    assert not any(k.startswith("mp_") for k in _keys(run))
    manifest = json.loads((lt.RUNS_DIR / f"run_{run['run_id']}.json").read_text(encoding="utf-8"))
    assert not any(k.startswith("mp_") for k in _keys(manifest))


def test_dft_and_unwired_tests_rejected(env):
    hid = _hyp()["hypothesis_id"]
    assert "error" in _spec(hid, "dft_validation")
    assert "error" in _spec(hid, "composition_proxy")
    yaml_text = (Path(__file__).resolve().parents[1] / "agents" / "discovery_loop.yaml").read_text(encoding="utf-8")
    callables = [l.split(":", 1)[1].strip() for l in yaml_text.splitlines() if l.strip().startswith("callable:")]
    assert callables and not any("dft" in c or "qe_gen" in c for c in callables)
    # the runner sub-agent exposes only run_stability_screen
    runner_block = yaml_text.split("  runner:")[1].split("  analyst:")[0]
    assert [c for c in callables if c in runner_block] == ["lab.loop_tools.run_stability_screen"]


def test_order_enforced_and_errors_returned(env):
    assert "error" in lt.evaluate_iteration(999)
    assert "error" in lt.record_next_decision(999, "narrow_downstream_to_survivors", "x", "y")
    assert "error" in lt.run_stability_screen(_hyp()["hypothesis_id"])  # a hypothesis is not a spec
    assert "error" in lt.register_hypothesis("x", "huge", "high")


def test_failed_controls_invalidate(env, monkeypatch):
    spec = _spec(_hyp()["hypothesis_id"])
    monkeypatch.setattr(lt, "_controls", lambda policy, c: [
        {"control_id": "positive_hull_0", "expected": "RETAIN", "observed": "DEPRIORITIZE"}])
    run = lt.run_stability_screen(spec["spec_id"])
    assert run["status"] == "invalid_controls_failed"
    ev = lt.evaluate_iteration(run["run_id"])
    assert ev["verdict"] == "invalid_controls_failed"
    assert ev["deterministic_decision_signal"] == "invalidate_run_no_strategy_update"
    bad = lt.record_next_decision(run["run_id"], "invalidate_run_no_strategy_update", "x", "y", "raise cutoff")
    assert "error" in bad
