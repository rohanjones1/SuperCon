# Milestones: build incrementally, one at a time

Supersedes the hour-by-hour order in PLAN_16H.md (keep its cut-lines and demo outline).
Each milestone = one Claude Code session. Claude writes files; **the human runs commands** and pastes output back.
Do not start a milestone until the previous one's "Done when" is true. Use `/clear` between milestones.

## M0: Environment check (one script, one command)

Claude writes: `pyproject.toml` (Python 3.12, deps listed in CLAUDE.md) and `scripts/check_env.py` that, in one run,
checks and prints a PASS/FAIL line for: Python version, each library import, `MAT_PROJECT_API` present, one structure
fetched from Materials Project, CHGNet loads and predicts on that structure (print seconds), SQLite write/read.
Human runs: create venv + install (human's own commands), then `python scripts/check_env.py`.
**Done when:** every line is PASS (or failures are pasted back for ONE diagnosis round).

## M1: Ledger

Claude writes: `lab/ledger.py` (SQLite, append-only helpers, tables from KB 04: start with prereg, candidates, runs, results,
controls, timing) and `tests/test_ledger.py`.
Human runs: `pytest tests/test_ledger.py`.
**Done when:** tests pass; a run can be recorded and read back with tool version, seed, wall-clock.

## M2: Data (cached, no repeated API calls)

Claude writes: `lab/data.py` + `scripts/build_dataset.py`: pull a bounded set (a few hundred entries) of known conventional
superconductors in the D1 family (from JARVIS/3DSC/MP; check field names first by printing one record) plus decoys
(non-superconductors in the same chemical space), assign **family-level** train/holdout splits, save to `data/processed/`.
Human runs: `python scripts/build_dataset.py` once; inspects counts printed at the end.
**Done when:** a table of seeds/decoys with split labels exists; no holdout family appears in train. Write prereg draft.

## M3: Stability engine (first real scientific result)

Claude writes: `lab/relax.py`, `lab/stability.py` (CHGNet relax, then E_above_hull vs MP competing phases), and
`scripts/calibrate_stability.py`: score ~30-50 known compounds, compare to MP DFT e_above_hull, print precision/recall/MAE.
Human runs: the calibration script.
**Done when:** a calibration table exists and the conventions (energy corrections) are confirmed or flagged in DECISIONS.md.
If CHGNet vs MP hull disagrees badly, STOP and fix the convention before building anything on top.

## M4: Controls, baselines, batch CLI

Claude writes: `lab/controls.py`, `lab/baselines.py` (B0 random, B1 heuristic), `lab/novelty.py`, `lab/metrics.py`,
`python -m lab.run_batch` (candidate batch in, scored results and control outcomes to ledger).
**Done when:** a 50-100 candidate batch runs with passing controls and everything is in the ledger.
(This is your standalone evidence engine and fallback if orchestration misbehaves.)

## M5: Omnigent hello-world (human drives the interactive part)

Human: start Omnigent, read its Agent YAML spec, and create/run a trivial agent. Claude only writes
`lab/tools_api.py` (one batch-level function) and `agents/hello.yaml` after the human pastes the relevant spec section.
**Done when:** an Omnigent agent calls `lab/tools_api.py` and returns its result. Record answers to KB 03 open items.

## M6: Thin agent loop

PI + Hypothesis + Runner + Analyst calling batch/ID tools. One full iteration in an Omnigent session, written to the ledger.

## M7: Intelligence

Planner (two competing tests, budget), strategy rules, Auditor (different vendor), approval policy.
Cut-lines in PLAN_16H.md apply.

## M8: Experiment, report, demo

Matched-budget agentic vs B0/B1, 3+ iterations, generated report, 2-minute demo.

## Prompt template for each milestone

> Read CLAUDE.md and docs/MILESTONES.md. Do ONLY milestone Mx. Follow the OPERATING PROTOCOL: write files, do not run commands,
> give me the exact command(s) to run, and stop at "Done when". Ask before reading anything beyond what the milestone names.
