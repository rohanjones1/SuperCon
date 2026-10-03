# SuperCon-Agentic Lab: context for Claude Code

## Mission
Hack-Nation x Databricks **Challenge 03: 10x Faster Scientific Discovery**. We have **16 hours total**. Build an
**Omnigent-orchestrated agentic lab** that screens candidate **ambient-pressure conventional (phonon-mediated)
superconductors**, produces **trustworthy, reproducible evidence**, and shows a result that **changes the next decision**.

Judging: 30% Omnigent orchestration, 25% breakthrough potential, 20% discovery acceleration and learning,
15% scientific rigor, 10% creativity and responsibility.
Submission: repo, agent specs and policies, 2-minute demo, cited evidence, experiment code and results,
measured improvement, next experiment.

## OPERATING PROTOCOL (highest priority, overrides everything below)
A previous session wasted money by looping on shell commands. These rules prevent that.
1. **The human runs all terminal commands.** You write and edit files, then give the exact command(s) for the human to run
   and wait for the pasted output. Do not run installs, clones, tests, servers, or network calls yourself.
2. **Interactive tools are human-only** (`omnigent` TUI/web UI, logins, anything that waits for input).
3. **One milestone per session** (see `docs/MILESTONES.md`). Stop at that milestone's "Done when" and report. Do not start the next one.
4. **No retry loops.** If the human pastes an error: explain the likely cause in at most 5 lines, propose ONE fix, and wait.
   Never try a second fix without new output from the human.
5. **Prefer one check script over many commands.** Write a single script that checks many things and prints a compact
   PASS/FAIL summary, rather than suggesting many separate commands.
6. **Cost guard:** if a task needs more than about 5 tool calls, stop and ask first. Do not read large files or whole repos
   speculatively; read only what the milestone names.
7. **Do not guess APIs.** If you need an Omnigent or library detail, ask the human to paste the relevant doc section or error.
8. At the end of each milestone, update `docs/STATUS.md` (what works, what's next, open issues) in under 15 lines.

## Working scientific question
Can a multi-fidelity agentic screen find ambient-pressure conventional superconductor candidates at lower compute
cost per validated hit than non-agentic baselines, without losing recall?
- **Arm A (retrospective, the trustworthy core):** hold out known superconductors and decoys, then measure ranking
  quality and funnel recall against baselines under a matched budget.
- **Arm B (prospective):** novel candidates, reported honestly as *hypotheses with uncertainty*, never as discoveries.
- Room-temperature (Tc >= 273 K) is the long-term moonshot, **not** a claim we make. Past room-temperature claims were
  retracted or never replicated, so reviewers reward rigor over hype.

## Non-negotiable principles
1. **LLMs decide; deterministic code computes.** No number in the ledger or any report may originate from an LLM.
2. **Pass IDs and batches, not blobs.** Agents exchange candidate IDs, batch IDs, and experiment specs.
   Structures live in the ledger or files. Tools are batch-level, because per-candidate calls hit tool-call caps and
   LLM latency dominates.
3. **The ledger is the single source of truth** (append-only SQLite). Reports are generated from it.
4. **Pre-register** hypotheses, metrics, thresholds, and holdout split before running. Lock and hash them.
5. **Controls in every batch** (positive and negative). A batch with failed controls is invalid.
6. **Evidence tiers T0-T4** label every claim. Never claim above the tier reached (see `docs/kb/02_science_and_trust.md`).
7. **Cite or omit.** No invented citations. Agent-generated hypotheses are labeled `agent_generated`.
8. **Report observed numbers**, including agent/LLM overhead and wall-clock. Never quote an unmeasured speedup.
9. **The science core (`lab/`) must not import Omnigent.** It has to run standalone, because Omnigent is alpha.
10. **Do not guess Omnigent's API.** Read the cloned repo's `docs/AGENT_YAML_SPEC.md`, `docs/POLICIES.md`, and
    `examples/` before writing any YAML or policy.

## Architecture (one screen)
Supervisor/PI agent delegates to sub-agents: **Evidence, Hypothesis, Planner, Runner, Analyst, Auditor
(different model vendor), Safety/Approval**. All write to the research ledger. The Analyst updates **strategy rules**
(retractable, evidence-linked), which the Hypothesis agent reads next iteration. A human approves tier promotions
and HPC script release. Full spec: `docs/kb/04_architecture_spec.md`.

## Repo layout
```
CLAUDE.md
docs/            kb/ (knowledge base), PLAN_16H.md, DECISIONS.md
lab/             pure-Python science core (no omnigent imports)
  ledger.py structures.py relax.py stability.py novelty.py controls.py
  tcproxy.py phonon.py(stretch) qe_gen.py baselines.py metrics.py
  tools_api.py   <- batch/ID-based functions exposed to agents
agents/          pi.yaml evidence.yaml hypothesis.yaml planner.yaml runner.yaml
                 analyst.yaml auditor.yaml safety.yaml  + policies
experiments/     prereg/*.yaml  runs/
data/            raw/ processed/   (large files gitignored)
reports/         figures + generated report
tests/
```

## Environment
- **Python 3.12+** (Omnigent requires it; the original blueprint's "3.10+" is wrong). Use `uv`.
- Libs: pymatgen, **mp-api** (the new MP client; legacy `pymatgen.ext.matproj` is deprecated), chgnet, ase, phonopy,
  matminer, scikit-learn, jarvis-tools, pandas, numpy, sqlite3.
- Env vars: `MAT_PROJECT_API` holds the Materials Project key. mp-api's own default name is `MP_API_KEY`, so read
  `MAT_PROJECT_API` in one helper (`lab/config.py`) and pass `api_key=` explicitly everywhere. Model credentials via `omnigent setup`.
- Check chgnet/pymatgen compatibility with Python 3.12 and a current torch early (see Plan hour 0).

## Known pitfalls (do not repeat)
- CHGNet `predict_structure` returns keys like `e`, `f`, `s`, `m` (verify). `e` is **total energy per atom, not formation
  energy**. "e < 0 means stable" is meaningless. Use relaxation, then **energy above hull** via
  pymatgen `PhaseDiagram` with MP competing phases (threshold about 25-50 meV/atom).
- CHGNet was trained on Materials Project data (MPtrj). MP-based checks are in-distribution and leak. Use family-level
  splits and independent references (OQMD, Alexandria, JARVIS, or our own coarse DFT).
- Stable does not mean superconducting. A Tc-relevant signal (metallicity/DOS, phonon stability, Tc proxy) is required.
- Seeds must be conventional, ambient-pressure superconductors. Avoid LaH10 (about 170 GPa) and cuprates (unconventional).
- Substitution without relaxation produces strained, wrongly-scored structures.
- QE metals need `occupations='smearing'`. Use SSSP pseudopotentials and cutoffs, and generate input with
  ASE/pymatgen writers, not f-strings.
- Dedupe with `StructureMatcher` and `reduced_formula`, not raw `formula`.

## How to work (Claude Code behavior)
- Work in **small verified steps**. After each module, give the human the exact test command to run (you do not run it).
- **Smoke-test integrations before building on them**, one at a time, per `docs/MILESTONES.md`.
- Fix seeds. Log tool and model versions, hardware, and wall-clock for every run.
- Prefer boring, working code over clever code. The clock is hard: respect the cut-lines in `docs/PLAN_16H.md`.
- When uncertain about a scientific or API fact, **write it to `docs/DECISIONS.md` as an open question** and
  proceed with the stated default. Do not silently invent.
- Never fabricate results, citations, or timing numbers. If a run fails, record the failure.
- Keep `README.md` current enough that a judge can reproduce the headline result with one command.

## Definition of done: minimum winning demo
1. Omnigent session shows multi-agent handoffs, a Planner choosing between at least two tests, and a visible approval gate.
2. A batch runs with passing controls; Analyst verdicts reference pre-registered predictions.
3. A strategy update visibly changes the next batch.
4. A matched-budget comparison (agentic vs baselines) with measured cost per validated hit.
5. A ledger-generated report, with every number traceable to a run ID, that states limits and required next validation.
