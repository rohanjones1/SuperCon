"""lab/governance.py — Auditor, human gate, decision record, iteration-2 planning, final report.

Extends the M6 loop (lab/loop_tools.py) after the Analyst:

    audit_iteration(run_id, claims_text="")      Auditor   deterministic integrity/boundary checks -> audit_id
    record_human_decision(run_id, audit_id, ...) HUMAN     approval gate (CLI only; not an agent tool) -> approval_id
    get_previous_decision()                      Planner   latest human decision record for iteration 2
    plan_next_iteration(previous_run_id, statement)        iteration-2 plan conditioned on the approved decision
    write_final_report(run_id)                   report    reports/discovery_report_run<id>.md

Ledger use (no schema change): audits and human decisions are `runs` rows (experiment_id = spec_id)
with one `controls` row per check; iteration plans are `prereg` rows (kind = "iteration_plan").
Write-once JSON artifacts in experiments/runs/ hold the structured records.

No omnigent, CHGNet, mp-api, network or DFT. Functions never raise; they return {"error": ...}.
Proposed rule changes are never applied by code: the screening policy file is never modified here.
"""
from __future__ import annotations

import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from lab import ledger as _ledger
from lab import loop_tools as _lt
from lab import test_menu as _tm
from lab import tools_api as _ta
from lab.decision import load_policy

TOOL_VERSION = "m7-governance-1"
HUMAN_CHOICES = ("APPROVE", "REJECT")
HELD_OUT_EXPERIMENT = {
    "next_experiment": "held_out_chgnet_validation",
    "status": "blocked",
    "reason": "requires held-out CHGNet computation not available in this lab",
}
BLOCKED_STATUS = "next_iteration_blocked_pending_held_out_validation"
VALIDATION_BOUNDARY = (
    "CHGNet triage is not DFT validation. RETAIN means worth computing DFT stability; DEPRIORITIZE "
    "does not mean unstable. The only DFT-derived truth here is the retrospective MP label, used "
    "in-sample. Held-out validation of the calibrated cutoff remains required."
)

# Forbidden-claim patterns (case-insensitive). A match containing a negation is ignored.
_FORBIDDEN = {
    "chgnet_described_as_dft": [
        r"dft[- ]validat\w*", r"validated (?:by|with|using) dft", r"dft[- ]confirmed",
        r"confirmed (?:by|with) dft", r"chgnet\W+(?:is|as|equals)\W+(?:a\W+)?dft",
    ],
    "retain_described_as_proven_stable": [
        r"\b(?:proven|confirmed|guaranteed|verified) stable\b", r"\bchgnet[- ]stable\b",
        r"retain\w*\b[^.\n]{0,40}\b(?:are|is) (?:thermodynamically )?stable\b",
    ],
    "deprioritize_described_as_unstable": [
        r"deprioriti[sz]\w*\b[^.\n]{0,40}\b(?:are|is|as|means|=)\s+(?:thermodynamically\s+)?unstable\b",
    ],
    "policy_presented_as_generalization": [
        r"generali[sz]\w*\s+(?:to|on)\s+(?:unseen|new|novel|held[- ]out)",
        r"prospective (?:performance|recall|precision)",
    ],
}
_NEGATION = re.compile(r"\bnot\b|n't\b|\bnever\b|\bno\b|\bwithout\b", re.I)
_METRIC_CLAIM = re.compile(r"(?:precision|recall)[^.\n]{0,40}?\b0\.\d{2,}", re.I)
_IN_SAMPLE = re.compile(r"in[- ]sample", re.I)


def _err(exc_or_msg) -> dict:
    return _lt._err(exc_or_msg)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_dir() -> Path:
    return Path(_lt.RUNS_DIR)


def _artifact(run_id: int, suffix: str = "") -> Path:
    return _runs_dir() / f"run_{int(run_id)}{suffix}.json"


def _load_optional(path: Path) -> dict | None:
    return _lt._read(path) if path.exists() else None


def _rel(path: Path) -> str:
    return str(path.relative_to(_lt._REPO_ROOT)) if path.is_relative_to(_lt._REPO_ROOT) else str(path)


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------

def scan_claims(text: str) -> list[str]:
    """Return failed boundary-check names for free text (forbidden claims, unlabeled metrics)."""
    failed = []
    for name, patterns in _FORBIDDEN.items():
        for pat in patterns:
            hit = any(not _NEGATION.search(m.group(0)) and not _NEGATION.search(text[max(0, m.start() - 12):m.start()])
                      for m in re.finditer(pat, text, re.I))
            if hit:
                failed.append(name)
                break
    if _METRIC_CLAIM.search(text) and not _IN_SAMPLE.search(text):
        failed.append("retrospective_metrics_without_in_sample_label")
    return failed


def audit_iteration(run_id: int, claims_text: str = "") -> dict:
    """Deterministic evidence-integrity and policy-compliance audit of one iteration."""
    try:
        run_id = int(run_id)
        passed: list[str] = []
        failed: list[str] = []
        warnings: list[str] = []

        def check(name: str, ok: bool) -> None:
            (passed if ok else failed).append(name)

        manifest = _load_optional(_artifact(run_id))
        ev = _load_optional(_artifact(run_id, "_evaluation"))
        nd = _load_optional(_artifact(run_id, "_next_decision"))
        check("artifact_run_manifest_exists", manifest is not None)
        check("artifact_evaluation_exists", ev is not None)
        check("artifact_next_decision_exists", nd is not None)
        if manifest is None:
            return _finish_audit(run_id, None, passed, failed, warnings, "run manifest missing")

        def ledger_ok(fn) -> bool:
            try:
                fn()
                return True
            except Exception:
                return False

        run_row = None
        try:
            run_row = _ledger.get_run(run_id, path=_lt.LEDGER_PATH)
        except Exception:
            pass
        check("ledger_run_id_exists", run_row is not None)
        check("ledger_run_links_spec", run_row is not None and run_row["experiment_id"] == manifest["spec_id"])
        check("spec_id_exists", ledger_ok(lambda: _lt._prereg("experiment_spec", manifest["spec_id"])))
        check("hypothesis_id_exists", ledger_ok(lambda: _lt._prereg("hypothesis", manifest["hypothesis_id"])))

        policy = load_policy(_lt.POLICY_PATH)
        check("frozen_convention_preserved",
              manifest["frozen_convention"] == policy["frozen_convention"] == "corrected")
        check("screening_cutoff_matches_policy",
              manifest["screening_cutoff_ev_per_atom"] == policy["screening_cutoff_ev_per_atom"])
        check("policy_calibration_run_matches",
              manifest["policy_calibration_run_id"] == policy["calibration_run_id"])

        controls = _ledger.get_controls(run_id, path=_lt.LEDGER_PATH)
        check("controls_recorded_and_passed", bool(controls) and all(bool(c["passed"]) for c in controls))
        check("runner_artifact_has_no_ground_truth",
              not any(k.startswith("mp_") for k in _lt_keys(manifest)))
        check("candidate_count_consistent_manifest",
              manifest["n_screened"] == len(manifest["candidates"])
              == manifest["n_retained"] + manifest["n_deprioritized"])

        eval_run_id = None
        if ev is not None:
            eval_run_id = ev.get("evaluation_run_id")
            eval_row = None
            try:
                eval_row = _ledger.get_run(int(eval_run_id), path=_lt.LEDGER_PATH)
            except Exception:
                pass
            check("evaluation_run_id_exists", eval_row is not None)
            check("ground_truth_used_only_after_screening",
                  eval_row is not None and run_row is not None and eval_row["timestamp"] >= run_row["timestamp"]
                  and eval_row["id"] > run_row["id"])
            check("metrics_trace_to_same_run",
                  ev["run_id"] == run_id and ev["spec_id"] == manifest["spec_id"]
                  and ev["counts"]["n_retained"] == manifest["n_retained"]
                  and ev["counts"]["n_screened"] == manifest["n_screened"])
            check("candidate_count_consistent_evaluation",
                  len(ev.get("traceability", {}).get("candidate_ids", [])) == manifest["n_screened"])
            rm = ev.get("retrospective_mp_metrics", {})
            check("retrospective_metrics_labeled_in_sample",
                  bool(_IN_SAMPLE.search(rm.get("label", ""))) and bool(_IN_SAMPLE.search(rm.get("warning", ""))))
            if ev["controls_passed"] is False:
                check("failed_controls_force_invalidation",
                      ev["deterministic_decision_signal"] == "invalidate_run_no_strategy_update")
            pol_counts = {k: policy.get(k) for k in ("tp", "fp", "fn", "tn")}
            if all(v is not None for v in pol_counts.values()):
                obs = {k: rm.get(k) for k in pol_counts}
                if obs != pol_counts:
                    warnings.append(
                        f"retrospective counts {obs} differ from stability_policy.json counts {pol_counts}; "
                        f"ground-truth column ({rm.get('mp_hull_column')}) may differ from the one used "
                        "for calibration (open assumption)")

        if nd is not None and ev is not None:
            check("next_decision_traces_to_run", nd["run_id"] == run_id)
            check("next_decision_follows_rule_or_justified",
                  nd["next_decision"] == ev["deterministic_decision_signal"] or bool(nd.get("rationale", "").strip()))
            if nd["next_decision"] != ev["deterministic_decision_signal"]:
                warnings.append("next_decision differs from deterministic signal; justified in rationale")
            check("rule_change_not_applied", nd.get("rule_change_status") == "proposed_not_applied")

        texts = {"claims_text": claims_text or ""}
        if nd is not None:
            texts.update({f"analyst_{k}": nd.get(k, "") for k in ("what_was_learned", "rationale", "proposed_rule_change")})
        if ev is not None:
            texts["hypothesis_statement"] = ev.get("hypothesis_statement", "")
        boundary_failures = sorted({f"{name}:{src}" for src, t in texts.items() for name in scan_claims(t)})
        check("scientific_boundary_language", not boundary_failures)
        failed.extend(boundary_failures)

        return _finish_audit(run_id, manifest, passed, failed, warnings, None, eval_run_id)
    except Exception as exc:
        return _err(exc)


def _lt_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _lt_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _lt_keys(v)


def _finish_audit(run_id, manifest, passed, failed, warnings, reason, eval_run_id=None) -> dict:
    veto = bool(failed)
    status = "VETO" if veto else "APPROVE"
    reason = reason or ("critical checks failed: " + ", ".join(failed) if veto else "all checks passed")
    _ledger.init_db(_lt.LEDGER_PATH)
    audit_id = _ledger.record_run(
        tool="lab.governance.audit_iteration", tool_version=TOOL_VERSION, model_checkpoint=None, seed=None,
        hardware=platform.platform(), wall_ms=None, path=_lt.LEDGER_PATH,
        experiment_id=manifest["spec_id"] if manifest else None,
    )
    for name in passed:
        _ledger.record_control(audit_id, f"audit:{name}", "pass", "pass", True, path=_lt.LEDGER_PATH)
    for name in failed:
        _ledger.record_control(audit_id, f"audit:{name}", "pass", "fail", False, path=_lt.LEDGER_PATH)
    out = {
        "audit_id": audit_id, "run_id": run_id, "evaluation_run_id": eval_run_id,
        "audit_status": status, "veto": veto, "reason": reason,
        "checks_passed": passed, "checks_failed": failed, "warnings": warnings,
        "promotion_allowed": not veto,
        "note": "Deterministic integrity audit. APPROVE permits the human gate; it is not a validation of any material.",
    }
    _lt._write_once(_artifact(run_id, f"_audit_{audit_id}"), out)
    if manifest is not None:
        write_final_report(run_id)
    return out


def _latest_audit(run_id: int) -> dict | None:
    paths = sorted(_runs_dir().glob(f"run_{int(run_id)}_audit_*.json"),
                   key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    return _lt._read(paths[-1]) if paths else None


# ---------------------------------------------------------------------------
# Human gate (called from scripts/human_gate.py, never by an agent)
# ---------------------------------------------------------------------------

def record_human_decision(
    run_id: int,
    audit_id: int,
    human_decision: str,
    approver: str,
    approve_rule_change: bool = False,
    note: str = "",
    channel: str = "cli_interactive",
) -> dict:
    """Persist the human APPROVE/REJECT of the Analyst's proposed next decision. One per run."""
    try:
        run_id, audit_id = int(run_id), int(audit_id)
        if human_decision not in HUMAN_CHOICES:
            return _err(f"human_decision must be one of {HUMAN_CHOICES}")
        if not approver or not str(approver).strip():
            return _err("approver is required")
        rec_path = _artifact(run_id, "_decision_record")
        if rec_path.exists():
            return _err(f"human decision already recorded for run {run_id}: {_rel(rec_path)}")
        audit_path = _artifact(run_id, f"_audit_{audit_id}")
        if not audit_path.exists():
            return _err(f"audit {audit_id} for run {run_id} not found")
        audit = _lt._read(audit_path)
        latest = _latest_audit(run_id)
        if latest and latest["audit_id"] != audit_id:
            return _err(f"audit {audit_id} is not the latest audit for run {run_id} (latest {latest['audit_id']})")
        if audit["veto"] and human_decision == "APPROVE":
            return _err("audit VETO: a vetoed run cannot be approved or promoted")
        nd = _load_optional(_artifact(run_id, "_next_decision"))
        ev = _load_optional(_artifact(run_id, "_evaluation"))
        if nd is None or ev is None:
            return _err("evaluation and next decision must exist before the human gate")

        approved = human_decision == "APPROVE"
        rule = nd.get("proposed_rule_change", "none") or "none"
        if rule.lower() == "none":
            rule_status = "none_proposed"
        elif approved and approve_rule_change:
            rule_status = "human_approved"
        else:
            rule_status = "proposed_not_applied"
        _ledger.init_db(_lt.LEDGER_PATH)
        manifest = _lt._read(_artifact(run_id))
        approval_id = _ledger.record_run(
            tool="lab.governance.human_gate", tool_version=TOOL_VERSION, model_checkpoint=None, seed=None,
            hardware=platform.platform(), wall_ms=None, path=_lt.LEDGER_PATH, experiment_id=manifest["spec_id"],
        )
        _ledger.record_control(approval_id, "human_gate", nd["next_decision"], human_decision, approved,
                               path=_lt.LEDGER_PATH)
        record = {
            "approval_id": approval_id, "run_id": run_id, "evaluation_run_id": ev["evaluation_run_id"],
            "audit_id": audit_id, "audit_status": audit["audit_status"],
            "human_gate_required": True, "channel": channel, "approver": str(approver).strip(),
            "timestamp": _now(),
            "status": "human_approved" if approved else "human_rejected",
            "human_approved": approved,
            "proposed_next_decision": nd["next_decision"],
            "approved_next_decision": nd["next_decision"] if approved else None,
            "proposal_label": "agent_generated",
            "verdict": ev["verdict"],
            "what_was_learned": nd.get("what_was_learned", ""),
            "rationale": nd.get("rationale", ""),
            "proposed_rule_change": rule,
            "rule_change_status": rule_status,
            "rule_applied": False,
            "rule_applied_note": "No code path applies rule text; the screening policy file is unchanged.",
            "constraints": {"spec_stop_rule": ev.get("spec_stop_rule", ""),
                            "frozen_convention": manifest["frozen_convention"],
                            "screening_cutoff_ev_per_atom": manifest["screening_cutoff_ev_per_atom"]},
            "next_experiment": HELD_OUT_EXPERIMENT,
            "strategic_progression": "allowed" if approved else "halted_by_human_rejection",
            "note": str(note)[:800],
        }
        _lt._write_once(rec_path, record)
        write_final_report(run_id)
        return record
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Iteration 2 input and plan
# ---------------------------------------------------------------------------

def get_previous_decision() -> dict:
    """Return the most recent human decision record (any status) for the next iteration."""
    try:
        paths = sorted(_runs_dir().glob("run_*_decision_record.json"),
                       key=lambda p: int(p.stem.split("_")[1]))
        if not paths:
            return {"status": "no_human_decision", "next_iteration_allowed": False,
                    "note": "Human gate pending; run scripts/human_gate.py."}
        rec = _lt._read(paths[-1])
        approved = rec["status"] == "human_approved"
        return {
            "previous_run_id": rec["run_id"], "evaluation_run_id": rec["evaluation_run_id"],
            "approval_id": rec["approval_id"], "audit_id": rec["audit_id"],
            "status": rec["status"], "next_iteration_allowed": approved,
            "previous_verdict": rec["verdict"], "what_was_learned": rec["what_was_learned"],
            "approved_next_decision": rec["approved_next_decision"],
            "approved_rule_change": rec["proposed_rule_change"] if rec["rule_change_status"] == "human_approved" else None,
            "rule_change_status": rec["rule_change_status"], "rule_applied": rec["rule_applied"],
            "constraints": rec["constraints"], "next_experiment": rec["next_experiment"],
            "source": _rel(paths[-1]),
        }
    except Exception as exc:
        return _err(exc)


def plan_next_iteration(previous_run_id: int, statement: str) -> dict:
    """Register an iteration-2 plan conditioned on the human-approved decision (no new compute)."""
    try:
        previous_run_id = int(previous_run_id)
        rec = _load_optional(_artifact(previous_run_id, "_decision_record"))
        if rec is None:
            return _err("no human decision recorded for this run; human gate pending")
        if rec["status"] != "human_approved":
            return {"status": "halted_by_human_rejection", "next_iteration_allowed": False,
                    "previous_run_id": previous_run_id, "approval_id": rec["approval_id"]}
        if not isinstance(statement, str) or not statement.strip():
            return _err("statement is required")
        manifest = _lt._read(_artifact(previous_run_id))
        prev_spec = _lt._prereg("experiment_spec", manifest["spec_id"])["content"]
        decision = rec["approved_next_decision"]

        if decision in ("narrow_downstream_to_survivors", "shift_to_ranking_within_retained"):
            focus_ids = list(manifest["validation_queue"])
            focus = "retained_subset"
        else:
            focus_ids, focus = [], "none"

        menu = _tm.get_test_menu(Path(_lt.CSV_PATH).parent)
        if "error" in menu:
            return _err(f"test menu unavailable: {menu['error']}")
        test_status = {}
        for name, t in menu["tests"].items():
            if not t["runnable_in_this_lab"]:
                test_status[name] = "not_runnable_in_this_lab"
            elif name == prev_spec["chosen_test"] and focus != "none":
                test_status[name] = "already_run_on_this_pool (previous spec stop_rule: do not repeat)"
            elif name not in _lt.RUNNABLE_TESTS:
                test_status[name] = "runnable_in_lab_but_not_wired_into_loop"
            else:
                test_status[name] = "runnable"
        test_status[HELD_OUT_EXPERIMENT["next_experiment"]] = f"blocked: {HELD_OUT_EXPERIMENT['reason']}"
        runnable = [n for n, s in test_status.items() if s == "runnable"]
        status = "ready" if runnable and focus_ids else BLOCKED_STATUS

        content = {
            "kind": "iteration_plan", "label": "agent_generated",
            "statement": statement.strip()[:1500],
            "conditioned_on": {
                "previous_run_id": previous_run_id, "approval_id": rec["approval_id"],
                "audit_id": rec["audit_id"], "previous_verdict": rec["verdict"],
                "approved_next_decision": decision,
                "approved_rule_change": rec["proposed_rule_change"] if rec["rule_change_status"] == "human_approved" else None,
            },
            "focus": focus, "focus_candidate_ids": focus_ids,
            "previous_pool_size": manifest["n_screened"],
            "test_status": test_status, "status": status,
            "next_experiment": HELD_OUT_EXPERIMENT,
            "registered_unix_s": time.time(),
        }
        _ledger.init_db(_lt.LEDGER_PATH)
        h = _ledger.record_prereg(content, path=_lt.LEDGER_PATH)
        plan_id = _ledger.get_prereg_id(h, path=_lt.LEDGER_PATH)
        out = {"plan_id": plan_id, **{k: v for k, v in content.items() if k not in ("kind", "registered_unix_s")},
               "focus_size": len(focus_ids)}
        _lt._write_once(_artifact(previous_run_id, "_iteration2_plan"), out)
        write_final_report(previous_run_id)
        return out
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------

def _f(x) -> str:
    return "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def write_final_report(run_id: int) -> dict:
    """Generate reports/discovery_report_run<id>.md from artifacts and the ledger (overwrites)."""
    try:
        run_id = int(run_id)
        m = _lt._read(_artifact(run_id))
        ev = _load_optional(_artifact(run_id, "_evaluation"))
        nd = _load_optional(_artifact(run_id, "_next_decision"))
        au = _latest_audit(run_id)
        rec = _load_optional(_artifact(run_id, "_decision_record"))
        plan = _load_optional(_artifact(run_id, "_iteration2_plan"))
        spec = _lt._prereg("experiment_spec", m["spec_id"])["content"]
        hyp = _lt._prereg("hypothesis", m["hypothesis_id"])["content"]
        menu = _tm.get_test_menu(Path(_lt.CSV_PATH).parent)
        tests = menu.get("tests", {})
        P = "pending"
        L = [
            f"# Discovery report: run {run_id}", "",
            "## Question",
            "Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool "
            "while retaining a high fraction of promising materials? (Stability gating is the bottleneck "
            "before expensive validation.)", "",
            "## Hypothesis (agent_generated, registered before the run)",
            hyp["statement"],
            f"- Predicted pool reduction: {hyp['predicted_pool_reduction']}; predicted recall of MP-stable: "
            f"{hyp['predicted_recall_of_mp_stable']}; frozen thresholds: {json.dumps(hyp['frozen_thresholds'])}", "",
            "## Alternatives",
            *[f"- {n} ({t.get('tier')}): runnable_in_this_lab={t.get('runnable_in_this_lab')}" for n, t in tests.items()],
            f"- Planner's reasoning (agent_generated): {spec['alternatives_considered']}", "",
            "## Experiment",
            f"- Selected: {spec['chosen_test']} (spec_id {m['spec_id']}); pool {spec['pool_id']}; "
            f"max_candidates (upper bound) {spec['max_candidates']}",
            f"- Why / expected learning (agent_generated): {spec['expected_learning']}",
            f"- Stop rule (agent_generated): {spec['stop_rule']}", "",
            "## Result",
            f"- Screened {m['n_screened']} of {m['n_pool']}; RETAINED BY CALIBRATED SCREENING {m['n_retained']}; "
            f"DEPRIORITIZED {m['n_deprioritized']}; validation queue {len(m['validation_queue'])}",
            f"- Controls: {'passed' if m['controls_passed'] else 'FAILED'} "
            f"({', '.join(c['control_id'] for c in m['controls'])})",
            f"- Runtime: {_f(m['wall_ms'])} ms (cache lookup, not CHGNet compute time)",
            f"- Cutoff {m['screening_cutoff_ev_per_atom']} eV/atom, frozen convention {m['frozen_convention']}, "
            f"calibration run {m['policy_calibration_run_id']}", "",
            "## Retrospective evidence (in-sample; NOT prospective performance)",
        ]
        if ev:
            r = ev["retrospective_mp_metrics"]
            L += [f"- TP {r['tp']}, FP {r['fp']}, FN {r['fn']}, TN {r['tn']}; precision {_f(r['retrospective_precision'])}, "
                  f"recall {_f(r['retrospective_recall'])} (MP hull <= 0.05 eV/atom, column {r['mp_hull_column']})",
                  f"- {r['warning']}", ""]
        else:
            L += [P, ""]
        L += ["## Analyst conclusion",
              (f"Verdict (computed): **{ev['verdict']}**; observed {json.dumps(ev['observed'])} vs predicted "
               f"{hyp['predicted_pool_reduction']}/{hyp['predicted_recall_of_mp_stable']}") if ev else P, "",
              "## Learning",
              f"- (agent_generated) {nd['what_was_learned']}" if nd else P]
        if plan:
            L.append(f"- Iteration 2 (plan_id {plan['plan_id']}): focus {plan['focus']} "
                     f"({plan['focus_size']} of {plan['previous_pool_size']} candidates); status {plan['status']}")
            L += [f"  - {n}: {s}" for n, s in plan["test_status"].items()]
        L += ["", "## Next decision"]
        if nd:
            L += [f"- Proposed (agent_generated): {nd['next_decision']} (deterministic signal "
                  f"{nd['deterministic_decision_signal']}; match {nd['matches_deterministic_signal']})",
                  f"- Proposed rule change: {nd['proposed_rule_change']} "
                  f"(status: {rec['rule_change_status'] if rec else nd['rule_change_status']}; applied: False)"]
        L += [f"- Next experiment: {HELD_OUT_EXPERIMENT['next_experiment']}, status {HELD_OUT_EXPERIMENT['status']}: "
              f"{HELD_OUT_EXPERIMENT['reason']}", "",
              "## Auditor",
              (f"{au['audit_status']} (audit_id {au['audit_id']}): {au['reason']}"
               + "".join(f"\n- warning: {w}" for w in au["warnings"])) if au else P, "",
              "## Human gate",
              (f"{'APPROVED' if rec['human_approved'] else 'REJECTED'} by {rec['approver']} at {rec['timestamp']} "
               f"(approval_id {rec['approval_id']}, channel {rec['channel']}); strategic progression: "
               f"{rec['strategic_progression']}") if rec else P, "",
              "## Validation boundary", VALIDATION_BOUNDARY, "",
              "## Traceability",
              f"- hypothesis_id {m['hypothesis_id']}; spec_id {m['spec_id']}; run_id {run_id}; "
              f"evaluation_run_id {ev['evaluation_run_id'] if ev else P}; audit_id {au['audit_id'] if au else P}; "
              f"approval_id {rec['approval_id'] if rec else P}; plan_id {plan['plan_id'] if plan else P}; "
              f"policy calibration run_id {m['policy_calibration_run_id']}",
              f"- Artifacts: experiments/runs/run_{run_id}*.json; ledger {_rel(Path(_lt.LEDGER_PATH))}", "",
              f"_{_ta.CAVEAT}_", ""]
        path = Path(_lt.REPORTS_DIR) / f"discovery_report_run{run_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(L), encoding="utf-8")
        return {"report": _rel(path)}
    except Exception as exc:
        return _err(exc)
