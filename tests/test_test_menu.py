"""Tests for lab/test_menu.py — tmp report files, no CHGNet, no network."""
from __future__ import annotations

import json
import math

import pytest

from lab import test_menu

ARM_A = {
    "point_estimates": {"proxy": {"auroc": 0.58, "enrichment": 1.05}},
    "bootstrap_ci": {"proxy": {"auroc_ci": [0.47, 0.66], "enrichment_ci": [0.41, 1.71]}},
}
CAL = {
    "metrics": {
        "b_hull_mae": {"corrected_ref": 0.038},
        "c_stability_classification_at_0.05": {
            "precision": 0.83, "precision_wilson_ci_95": [0.55, 0.95],
            "recall": 0.67, "recall_wilson_ci_95": [0.42, 0.85],
        },
    },
    "verdict": {"message": "NOT USABLE AS-IS"},
}
POLICY = {
    "screening_cutoff_ev_per_atom": 0.0585,
    "precision_at_cutoff": 0.75, "precision_wilson_ci_95": [0.51, 0.90],
    "recall_at_cutoff": 0.80, "recall_wilson_ci_95": [0.55, 0.93],
}
# wall_ms 2000, 4000, 6000 usable -> median 4.0 s; failed row ignored.
CSV = (
    "material_id,wall_ms,error\n"
    "mp-1,2000,\n"
    "mp-2,6000,\n"
    "mp-3,4000,\n"
    "mp-4,99999,relax failed\n"
)
BUDGET = "candidate_pool_size: 200\ntotal_compute_budget_s: 900\nnote: \"placeholder\"\n"


@pytest.fixture
def rdir(tmp_path):
    (tmp_path / test_menu.ARM_A_FILE).write_text(json.dumps(ARM_A), encoding="utf-8")
    (tmp_path / test_menu.CALIBRATION_FILE).write_text(json.dumps(CAL), encoding="utf-8")
    (tmp_path / test_menu.POLICY_FILE).write_text(json.dumps(POLICY), encoding="utf-8")
    (tmp_path / test_menu.CALIBRATION_CSV).write_text(CSV, encoding="utf-8")
    (tmp_path / "budget.yaml").write_text(BUDGET, encoding="utf-8")
    return tmp_path


def test_numbers_come_from_files(rdir):
    out = test_menu.get_test_menu(rdir)
    t = out["tests"]
    assert t["composition_proxy"]["measured_performance"]["auroc"]["value"] == 0.58
    ct = t["chgnet_triage"]
    assert ct["pre_registered_at_0.05"]["recall"]["value"] == 0.67
    assert ct["pre_registered_at_0.05"]["verdict"]["value"] == "NOT USABLE AS-IS"
    assert ct["deployed_policy_in_sample"]["recall"]["wilson_ci_95"] == [0.55, 0.93]
    assert ct["measured_cost_s_per_candidate"]["value"] == pytest.approx(4.0)
    assert ct["measured_cost_s_per_candidate"]["n_rows_used"] == 3
    assert ct["measured_cost_s_per_candidate"]["scope"] == "relaxation only"
    assert "source" in ct["pre_registered_at_0.05"]["hull_mae_ev_per_atom"]

    # change a value in the file -> output changes
    (rdir / test_menu.POLICY_FILE).write_text(json.dumps({**POLICY, "recall_at_cutoff": 0.9}), encoding="utf-8")
    out2 = test_menu.get_test_menu(rdir)
    assert out2["tests"]["chgnet_triage"]["deployed_policy_in_sample"]["recall"]["value"] == 0.9


def test_proxy_ci_limit_derived_from_data(rdir):
    lim = test_menu.get_test_menu(rdir)["tests"]["composition_proxy"]["limits"]
    assert any("includes 0.5" in s for s in lim)
    arm = json.loads(json.dumps(ARM_A))
    arm["bootstrap_ci"]["proxy"]["auroc_ci"] = [0.55, 0.66]
    (rdir / test_menu.ARM_A_FILE).write_text(json.dumps(arm), encoding="utf-8")
    lim = test_menu.get_test_menu(rdir)["tests"]["composition_proxy"]["limits"]
    assert not any("includes 0.5" in s for s in lim)


def test_dft_not_runnable(rdir):
    dft = test_menu.get_test_menu(rdir)["tests"]["dft_validation"]
    assert dft["runnable_in_this_lab"] is False
    assert dft["tier"] == "T3"
    assert dft["measured_performance"] is None and dft["measured_cost_s_per_candidate"] is None


def test_budget_math(rdir):
    out = test_menu.get_budget(rdir / "budget.yaml", rdir)
    assert out["affordable_chgnet_candidates_upper_bound"] == math.floor(900 / 4.0)  # 225
    assert out["coverage_fraction_upper_bound"] == 1.0                              # min(1, 225/200)
    assert out["cost_basis_note"] == test_menu.COST_BASIS_NOTE
    assert "affordable_chgnet_candidates" not in out and "coverage_fraction" not in out
    assert out["spent_s"] == 0 and out["spend_tracking"] == "not wired yet"
    (rdir / "budget.yaml").write_text("candidate_pool_size: 1000\ntotal_compute_budget_s: 900\n", encoding="utf-8")
    out = test_menu.get_budget(rdir / "budget.yaml", rdir)
    assert out["coverage_fraction_upper_bound"] == pytest.approx(225 / 1000)


def test_missing_file_returns_error(rdir):
    (rdir / test_menu.CALIBRATION_FILE).unlink()
    assert "error" in test_menu.get_test_menu(rdir)
    assert "error" in test_menu.get_budget(rdir / "nope.yaml", rdir)
