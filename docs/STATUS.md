# Status

## Works
- T1 calibrated CHGNet screen, run 8 reports, auditor VETO, human gate still pending.
- Static judge demo: `web/index.html` reads `web/data/snapshot.json` (no ledger, no Omnigent in the page).
- Exporter: `uv run python scripts/export_demo_snapshot.py --run-id 8`.

## Next
- Human runs the gate only if they choose to: `uv run python scripts/human_gate.py --run-id 8`.
- Held-out CHGNet validation is blocked in this lab. Iteration 2 plan_id is pending.

## Open
- Retrospective run counts (TP 11) differ from `stability_policy.json` (TP 12). Shown side by side.
- Auditor VETO on run 8 is unchanged.
