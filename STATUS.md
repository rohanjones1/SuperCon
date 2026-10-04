# Status
- Current milestone: **M7 governance: Auditor + human gate + iteration 2 (written, awaiting test run)**
- M0-M6 done. M6 run 6 (in-sample): 39 pool, 15 RETAIN / 24 DEPRIORITIZE, P 0.733, R 0.786, controls passed,
  verdict not_supported, next decision narrow_downstream_to_survivors.
- M7: lab/governance.py: audit_iteration (APPROVE/VETO, audit_id), record_human_decision (CLI only, approval_id),
  get_previous_decision, plan_next_iteration (iteration 2, plan_id), write_final_report -> reports/discovery_report_run<id>.md.
- scripts/human_gate.py (interactive gate); scripts/run_iteration_offline.py extended (audit, gate flag, --iteration2-run-id).
- agents/discovery_loop.yaml: + auditor, next_iteration_planner sub-agents; PI stops at VETO and at the human gate.
- tests/test_governance.py: 11 tests. Ledger schema unchanged (D21).
- Open: Auditor will warn that run-6 counts (TP 11, FN 3) differ from stability_policy.json (TP 12, FN 3):
  likely the assumed GT column mp_energy_above_hull_summary vs the calibration's column. Not fixed (needs your call).
- Open: native Omnigent approval policy unverified; Auditor same vendor (D6); held-out validation blocked (no compute tool).
- Next: pytest, gate run 6, iteration 2, then the demo.
