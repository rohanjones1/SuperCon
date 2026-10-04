# SuperCon

Can a calibrated CHGNet screening layer shrink the pool sent to expensive validation and still keep most materials that look promising on a retrospective Materials Project hull label? This repo is a T1 triage demo for Hack-Nation Challenge 03. It does not claim a superconductor, a DFT result, or a room-temperature material.

## Run 8 (in-sample)

Screened 39 of 39 cached structures. **15 RETAINED BY CALIBRATED SCREENING**, **24 DEPRIORITIZE**, validation queue 15. Retrospective comparison with MP hull ≤ 0.05 eV/atom on column `mp_energy_above_hull_summary`: TP 11, FP 4, FN 3, TN 21, precision 0.733, recall 0.786. The cutoff was calibrated on this same pool, so those rates are a consistency check, not prospective performance. Computed verdict: **not_supported** (predicted minor pool reduction and high recall; observed substantial reduction and recall not high). Next decision: `narrow_downstream_to_survivors`. Auditor **VETO** (audit_id 10). Human gate **pending**. Held-out validation is blocked.

`stability_policy.json` records TP 12 / TN 20 on a different ground-truth column. Both counts are shown. Do not pick one silently.

CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data.

Arm A (family-held-out composition proxy vs baselines) did not beat simple baselines on this split; confidence intervals overlap. It is not the headline.

No 10× speedup is claimed. Run 8’s wall time is a cache lookup, not CHGNet compute.

## Omnigent

Agents and the handoff order live in `agents/discovery_loop.yaml` (PI, hypothesis, planner, runner, analyst, auditor, human gate, next-iteration planner). Run the discovery_loop agent in Omnigent as already used in this repo. This README does not add CLI flags.

## Offline iteration

Same tool sequence without an LLM, writing the local ledger:

```bash
uv run python scripts/run_iteration_offline.py
```

Run JSON is kept in `experiments/runs/`. The SQLite ledger stays local (`*.sqlite` is still gitignored) and is not served to the demo page.

## Demo

Export the committed snapshot (uses run JSON when present, otherwise the reports plus a read-only screen of the calibration CSV):

```bash
uv run python scripts/export_demo_snapshot.py --run-id 8
```

Open the page with a static server. `fetch` fails on `file://`.

```bash
python -m http.server -d web 8000
```

or `npx serve web`.

## Vercel

Import the repo. Output directory `web`. No install, no build, no environment variables, no Python runtime. `web/data/snapshot.json` must already be committed. The SQLite ledger is local-only and is not served.

## Limits

Evidence tier T1 only. Run 8 is vetoed. The human gate is pending (`uv run python scripts/human_gate.py --run-id 8`). Iteration 2 has no plan_id. `held_out_chgnet_validation` is blocked because this lab cannot run held-out CHGNet. Arm A is not a win.

## Traceability (run 8)

hypothesis_id 10 · spec_id 11 · run_id 8 · evaluation_run_id 9 · audit_id 10 · approval_id pending · plan_id pending · policy calibration run_id 3.

Every number on the demo page comes from `web/data/snapshot.json`, which the exporter fills from those artifacts. LLMs do not compute metrics.
