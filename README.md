# SuperCon

A small agentic lab that screens cached materials with a calibrated CHGNet energy-above-hull gate, then records whether that gate is worth using before a more expensive check. The question is:

Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool while retaining a high fraction of promising materials?

Or in simple terms, can we shorten the process of finding a suitable material for room temperature superconductors by using AI agents experimentation work loops to filter out incompatible and unstable material formulations?

This is evidence tier **T1**. It does not claim a superconductor, a DFT result, or a room-temperature material. CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data.

The checked-in demo is **run 8**. A clone-and-run creates a **new** iteration with new IDs. Both use the same lab tools. Numbers are computed by that code and written to the local ledger. They are not typed by an LLM.

## What the pipeline does

One discovery passes integer IDs only, in this order:

1. Hypothesis registers a prediction before any screening.
2. Planner chooses one test from the menu (`chgnet_triage` is the test this loop can execute).
3. Runner screens the cached pool and checks controls.
4. Analyst compares the prediction with the run and records the next decision.
5. Auditor returns APPROVE or VETO.
6. A human types the gate in a terminal. Agents cannot approve.
7. Iteration 2 is planned only after that human decision. `held_out_chgnet_validation` stays blocked: this lab has no held-out CHGNet tool.

## Run it from a fresh clone

Requirements: Git, [uv](https://docs.astral.sh/uv/), Python 3.12 (`.python-version`). Run every command from the repository root.

```bash
git clone https://github.com/rohanjones1/SuperCon.git
cd SuperCon
uv sync
```

`uv sync` installs the project libraries. The discovery command below does not call CHGNet, the Materials Project API, or the network. It reads hulls from `reports/stability_calibration.csv` and the cutoff from `reports/stability_policy.json`. You do not need `MAT_PROJECT_API` for this path.

Start one discovery. This creates `data/ledger.sqlite` (gitignored) and writes `experiments/runs/run_<id>*.json` plus a report under `reports/`.

```bash
uv run python scripts/run_iteration_offline.py
```

Read the printed blocks. Each one is a tool result. Keep the `run_id` from the runner step.

- If the auditor prints **VETO**, the script stops. That run is not promoted. The reason is in the audit block.
- If the auditor does not veto, the script stops at the human gate and prints the next two commands. Run the gate in a terminal and type `APPROVE` or `REJECT` when asked. A vetoed audit accepts only `REJECT`.

```bash
uv run python scripts/human_gate.py --run-id <run_id>
uv run python scripts/run_iteration_offline.py --iteration2-run-id <run_id>
```

Use the `run_id` the first command printed. After a rejection, iteration 2 halts. After an approval, iteration 2 still cannot run held-out CHGNet. Expect a blocked next experiment, not a new validation result.

The same tool sequence with live agents is `agents/discovery_loop.yaml`. Omnigent is separate software (`omnigent setup`, then `omnigent run agents/discovery_loop.yaml`, as in its own docs). The PI is instructed to stop at a VETO and at the human gate. The offline script is the path that runs the lab tools without an LLM.

## Look at the recorded demo

The site shows the committed snapshot of run 8. `fetch` fails if you open the HTML file directly.

```bash
python -m http.server -d web 8000
```

Open `http://localhost:8000`. **Run one discovery** on that page reloads `web/data/snapshot.json` and walks the recorded handoff. It does not start the pipeline above.

To point the page at a run you just created:

```bash
uv run python scripts/export_demo_snapshot.py --run-id <run_id>
```

Refresh the local server. The Vercel site stays on whatever snapshot is committed in `web/data/snapshot.json`.

## Checked-in run 8

Screened 39 of 39. **15 RETAINED BY CALIBRATED SCREENING**, **24 DEPRIORITIZE**, validation queue 15. Retrospective MP hull ≤ 0.05 eV/atom on column `mp_energy_above_hull_summary`: TP 11, FP 4, FN 3, TN 21, precision 0.733, recall 0.786. Those rates are in-sample. The cutoff was fit on this same pool. Computed verdict: **not_supported**. Next decision: `narrow_downstream_to_survivors`. Auditor **VETO** (audit_id 10). Human gate **pending**.

`reports/stability_policy.json` records TP 12 and TN 20. The demo shows both. Do not collapse them into one count.

Arm A, the family-held-out composition proxy, did not beat simple baselines on its split. It is not the headline. No speedup number is claimed. Run 8’s wall time is a cache lookup, not CHGNet compute.

Traceability for that published run: hypothesis_id 10, spec_id 11, run_id 8, evaluation_run_id 9, audit_id 10, approval_id pending, plan_id pending, policy calibration run_id 3.

## Deploy the page

Vercel serves the static `web/` directory. No Python runtime and no environment variables. Details are in `web/README.md`.
