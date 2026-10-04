"""Run one discovery iteration through lab.loop_tools WITHOUT Omnigent or any LLM.

Same tool sequence as agents/discovery_loop.yaml, with fixed scripted agent text
(labelled scripted_offline). Fallback/reference for the Omnigent session; writes to the real ledger.

    uv run python scripts/run_iteration_offline.py                     # iteration 1 + audit, stops at human gate
    uv run python scripts/run_iteration_offline.py --human-decision APPROVE --approver <you>
                                                                       # iteration 1 + audit + gate + iteration 2
    uv run python scripts/run_iteration_offline.py --iteration2-run-id 6
                                                                       # iteration 2 after scripts/human_gate.py
"""
from __future__ import annotations

import argparse
import json
import sys

from lab import governance as gov
from lab import loop_tools as lt
from lab import test_menu as tm


def _step(name: str, out: dict) -> dict:
    print(f"\n=== {name} ===")
    print(json.dumps(out, indent=2, default=str)[:4000])
    if "error" in out:
        print(f"\nSTOP: {name} returned an error.")
        sys.exit(1)
    return out


ITER2_STATEMENT = ("[scripted_offline] Conditioned on the human-approved decision from run {run_id} "
                   "({decision}), focus downstream work on the retained subset instead of re-running "
                   "chgnet_triage on the same pool.")


def _iteration2(run_id: int) -> None:
    prev = _step("next-iteration planner: get_previous_decision", gov.get_previous_decision())
    plan = _step("next-iteration planner: plan_next_iteration", gov.plan_next_iteration(
        run_id, ITER2_STATEMENT.format(run_id=run_id, decision=prev.get("approved_next_decision"))))
    print(f"
Iteration 2 status: {plan.get('status')}")
    print(f"Report: {gov.write_final_report(run_id).get('report')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--human-decision", choices=["APPROVE", "REJECT"],
                    help="human gate decision, typed by the human on the command line")
    ap.add_argument("--approver", help="required with --human-decision")
    ap.add_argument("--iteration2-run-id", type=int,
                    help="skip iteration 1; plan iteration 2 for a run already gated via scripts/human_gate.py")
    args = ap.parse_args()
    if args.iteration2_run_id is not None:
        _iteration2(args.iteration2_run_id)
        return
    if args.human_decision and not args.approver:
        ap.error("--approver is required with --human-decision")

    pool = _step("hypothesis: get_candidate_pool", lt.get_candidate_pool())
    hyp = _step("hypothesis: register_hypothesis", lt.register_hypothesis(
        statement=("[scripted_offline] The calibrated CHGNet stability gate will retain a substantially "
                   f"smaller subset of the {pool['n_candidates']}-compound pool while retaining most "
                   "candidates that satisfy the MP stability criterion."),
        predicted_pool_reduction="substantial",
        predicted_recall_of_mp_stable="high",
    ))
    _step("planner: get_test_menu", tm.get_test_menu())
    _step("planner: get_budget", tm.get_budget())
    spec = _step("planner: register_experiment_spec", lt.register_experiment_spec(
        hypothesis_id=hyp["hypothesis_id"],
        chosen_test="chgnet_triage",
        alternatives_considered="[scripted_offline] composition_proxy: AUROC CI includes 0.5. dft_validation: not runnable.",
        expected_learning="[scripted_offline] Whether the gate shrinks the validation pool without dropping most MP-stable compounds.",
        stop_rule="[scripted_offline] Stop if controls fail or any selected candidate is missing from the cache.",
        unknowns="[scripted_offline] End-to-end cost per novel candidate is unmeasured.",
    ))
    run = _step("runner: run_stability_screen", lt.run_stability_screen(spec["spec_id"]))
    ev = _step("analyst: evaluate_iteration", lt.evaluate_iteration(run["run_id"]))
    _step("analyst: record_next_decision", lt.record_next_decision(
        run_id=run["run_id"],
        next_decision=ev["deterministic_decision_signal"],
        what_was_learned=(f"[scripted_offline] Verdict {ev['verdict']}: {ev['counts']['n_retained']} of "
                          f"{ev['counts']['n_screened']} retained by calibrated screening (in-sample)."),
        rationale="[scripted_offline] Follows the deterministic decision signal and the spec stop rule.",
        proposed_rule_change="none",
    ))
    audit = _step("auditor: audit_iteration", gov.audit_iteration(run["run_id"]))
    if audit["veto"]:
        print("
STOP: Auditor VETO. Run is not promoted.")
        sys.exit(1)
    if not args.human_decision:
        print(f"
HUMAN GATE: uv run python scripts/human_gate.py --run-id {run['run_id']}")
        print(f"then:       uv run python scripts/run_iteration_offline.py --iteration2-run-id {run['run_id']}")
        return
    rec = _step("human gate (offline flag)", gov.record_human_decision(
        run["run_id"], audit["audit_id"], args.human_decision, args.approver, channel="offline_cli_flag"))
    if not rec["human_approved"]:
        print("
STOP: human rejected the decision; strategic progression halted.")
        print(f"Report: {gov.write_final_report(run['run_id']).get('report')}")
        return
    _iteration2(run["run_id"])


if __name__ == "__main__":
    main()
