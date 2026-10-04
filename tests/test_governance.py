"""Tests for lab/governance.py — Auditor, human gate, decision record, iteration 2, report.

Reuses the tmp env from tests/test_loop_tools.py. No CHGNet, no network, no Omnigent, no LLM.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lab import governance as gov
from lab import ledger, loop_tools as lt
from tests.test_loop_tools import _hyp, _spec, env  # noqa: F401  (env is a fixture)

ROOT = Path(__file__).resolve().parents[1]


def _iteration1(rule: str = "none") -> int:
    spec = _spec(_hyp()["hypothesis_id"])
    run = lt.run_stability_screen(spec["spec_id"])
    ev = lt.evaluate_iteration(run["run_id"])
    lt.record_next_decision(run["run_id"], ev["deterministic_decision_signal"],
                            "retained 2 of 4 (in-sample)", "follows signal", rule)
    return run["run_id"]


def _approve(run_id: int, decision: str = "APPROVE", **kw) -> dict:
    audit = gov.audit_iteration(run_id)
    return gov.record_human_decision(run_id, audit["audit_id"], decision, "tester", **kw)


def test_audit_approves_valid_run(env):
    out = gov.audit_iteration(_iteration1())
    assert out["audit_status"] == "APPROVE" and out["veto"] is False, out["checks_failed"]
    assert "ground_truth_used_only_after_screening" in out["checks_passed"]
    assert "candidate_count_consistent_evaluation" in out["checks_passed"]


def test_audit_vetoes_missing_run(env):
    out = gov.audit_iteration(999)
    assert out["audit_status"] == "VETO" and out["veto"] is True
    assert "artifact_run_manifest_exists" in out["checks_failed"]


def test_audit_catches_dft_claim(env):
    run_id = _iteration1()
    out = gov.audit_iteration(run_id, "The retained candidates are DFT-validated.")
    assert out["veto"] and "chgnet_described_as_dft:claims_text" in out["checks_failed"]
    # negated caveats are not flagged
    assert gov.scan_claims(lt._ta.CAVEAT) == []
    assert gov.scan_claims("RETAIN is not proven stable; DEPRIORITIZE does not mean unstable.") == []
    assert "deprioritize_described_as_unstable" in gov.scan_claims("Deprioritized compounds are unstable.")


def test_audit_catches_metrics_without_in_sample(env):
    run_id = _iteration1()
    out = gov.audit_iteration(run_id, "Retrospective recall 0.786 and precision 0.733.")
    assert out["veto"]
    assert "retrospective_metrics_without_in_sample_label:claims_text" in out["checks_failed"]
    assert gov.scan_claims("In-sample recall 0.786 and precision 0.733.") == []


def test_human_approval_persisted(env):
    run_id = _iteration1()
    rec = _approve(run_id)
    assert rec["status"] == "human_approved" and rec["human_gate_required"] is True
    for k in ("approval_id", "run_id", "evaluation_run_id", "proposed_next_decision",
              "approved_next_decision", "timestamp"):
        assert rec[k] is not None
    assert (lt.RUNS_DIR / f"run_{run_id}_decision_record.json").exists()
    ctl = ledger.get_controls(rec["approval_id"], path=lt.LEDGER_PATH)
    assert ctl[0]["control_id"] == "human_gate" and ctl[0]["passed"] == 1
    assert "error" in _approve(run_id)  # write-once: second decision refused


def test_rejection_halts_progression(env):
    run_id = _iteration1()
    rec = _approve(run_id, "REJECT")
    assert rec["status"] == "human_rejected" and rec["approved_next_decision"] is None
    assert gov.get_previous_decision()["next_iteration_allowed"] is False
    assert gov.plan_next_iteration(run_id, "try anyway")["status"] == "halted_by_human_rejection"


def test_veto_cannot_be_approved(env):
    run_id = _iteration1()
    audit = gov.audit_iteration(run_id, "These are DFT-validated.")
    assert "error" in gov.record_human_decision(run_id, audit["audit_id"], "APPROVE", "tester")


def test_approved_decision_feeds_next_iteration(env):
    run_id = _iteration1()
    _approve(run_id)
    prev = gov.get_previous_decision()
    assert prev["previous_run_id"] == run_id and prev["next_iteration_allowed"] is True
    assert prev["approved_next_decision"] == "narrow_downstream_to_survivors"
    assert prev["what_was_learned"] and prev["constraints"]["spec_stop_rule"]
    plan = gov.plan_next_iteration(run_id, "focus on survivors")
    assert plan["conditioned_on"]["previous_run_id"] == run_id
    assert plan["focus"] == "retained_subset" and plan["focus_candidate_ids"] == ["mp-a", "mp-b"]
    assert plan["focus_size"] < plan["previous_pool_size"]
    assert plan["test_status"]["chgnet_triage"].startswith("already_run")


def test_rule_change_stays_unapplied(env):
    policy_before = Path(lt.POLICY_PATH).read_bytes()
    run_id = _iteration1(rule="lower the cutoff")
    rec = _approve(run_id)  # approve_rule_change defaults to False
    assert rec["rule_change_status"] == "proposed_not_applied" and rec["rule_applied"] is False
    assert gov.get_previous_decision()["approved_rule_change"] is None

    run2 = _iteration1(rule="lower the cutoff")
    rec2 = _approve(run2, approve_rule_change=True)
    assert rec2["rule_change_status"] == "human_approved" and rec2["rule_applied"] is False
    assert Path(lt.POLICY_PATH).read_bytes() == policy_before


def test_blocked_when_held_out_unavailable(env):
    run_id = _iteration1()
    _approve(run_id)
    plan = gov.plan_next_iteration(run_id, "focus on survivors")
    assert plan["status"] == gov.BLOCKED_STATUS
    assert plan["next_experiment"] == gov.HELD_OUT_EXPERIMENT
    report = (Path(lt.REPORTS_DIR) / f"discovery_report_run{run_id}.md").read_text(encoding="utf-8")
    for section in ("## Question", "## Hypothesis", "## Alternatives", "## Experiment", "## Result",
                    "## Retrospective evidence", "## Analyst conclusion", "## Learning", "## Next decision",
                    "## Auditor", "## Human gate", "## Validation boundary", "## Traceability"):
        assert section in report
    assert "held_out_chgnet_validation" in report and "APPROVED" in report and "plan_id" in report


def test_no_dft_in_new_path():
    banned = ("chgnet", "mp_api", "pymatgen", "ase", "subprocess", "qe_gen", "vasp", "espresso", "omnigent")
    for rel in ("lab/governance.py", "lab/loop_tools.py", "scripts/human_gate.py", "scripts/run_iteration_offline.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        mods = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        mods += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(m.split(".")[0] in banned or any(b in m for b in ("qe_gen", "dft")) for m in mods), (rel, mods)
    yaml_text = (ROOT / "agents" / "discovery_loop.yaml").read_text(encoding="utf-8")
    callables = [l.split(":", 1)[1].strip() for l in yaml_text.splitlines() if l.strip().startswith("callable:")]
    assert "lab.governance.audit_iteration" in callables
    assert not any("dft" in c or "qe_gen" in c or "record_human_decision" in c for c in callables)
