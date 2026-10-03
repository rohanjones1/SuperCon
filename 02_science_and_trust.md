# KB 02: Science and trust methodology

## 1. Scope decision (working default; change in DECISIONS.md)
Conventional (electron-phonon, BCS/Eliashberg) superconductors at **ambient pressure**, restricted to one chemical
neighborhood (default: metal borides, carbides, silicides, and simple intermetallics, MgB2-like and A15-like families).
Why: DFT + phonon methods are valid for this class, training data exists, and hydrides need megabar pressure while
cuprates are unconventional.

## 2. Evidence tiers (label every candidate and claim)
| Tier | Evidence | In this project |
|---|---|---|
| T0 | Agent-generated hypothesis | yes |
| T1 | ML surrogate: relaxed energy / E_hull, Tc proxy | yes (core) |
| T2 | Coarse DFT or phonon stability check | stretch / small calibration subset |
| T3 | Full DFT + electron-phonon (HPC) | **script generation only, not run** |
| T4 | Synthesis / measurement | proposed next step only |
Rule: a claim may never exceed the highest tier actually reached. Final language: "candidate predicted at T1",
never "superconductor found".

## 3. Stability, done correctly
1. Relax structure with CHGNet `StructOptimizer` (substitutions arrive strained).
2. Get energy per atom (CHGNet `e`; verify key names). Treat as total energy, not formation energy.
3. Build competing-phase set from Materials Project for the chemical system; compute **E_above_hull** with
   pymatgen `PhaseDiagram`. Stable-ish threshold: <= 0.025-0.05 eV/atom (pre-register the value).
4. Report uncertainty (ensemble or perturbation spread if available). Novel chemistries are out-of-distribution.
Mixing CHGNet energies with MP DFT energies needs consistent corrections; use the MP-compatible CHGNet convention
and **verify** it before trusting hull distances. Validate on the calibration set below.

## 4. Tc-relevant signals (stability alone says nothing about superconductivity)
- **Metallicity / DOS at E_F:** needed; superconductors here are metals (coarse DFT, or a classifier).
- **Dynamical stability:** CHGNet-driven finite-displacement phonons via phonopy (stretch goal); imaginary modes -> flag.
- **Tc proxy (ranking only):** ML model on composition (+ simple structure) features, trained on SuperCon/3DSC and/or
  JARVIS electron-phonon data. Use **leave-family-out** validation. Expect weak generalization; never present as a Tc prediction.
- Allen-Dynes (needs lambda, omega_log from e-ph calcs, so T3): Tc = (w_log/1.2) * exp[-1.04(1+l) / (l - mu*(1+0.62 l))].

## 5. Datasets and sources (verify field names and access on first use)
- Materials Project (structures, hull data): needs `MP_API_KEY`, use `mp-api`.
- JARVIS-DFT via `jarvis-tools` (includes electron-phonon superconductivity data for roughly 1000 materials).
- 3DSC: superconductors matched to crystal structures (Sommer et al., Scientific Data 2023).
- SuperCon (NIMS): experimental Tc records.
- Independent hull references: OQMD, Alexandria.
- OpenAlex: literature and citations.

## 6. Experimental design (the trust core)
**Pre-registration** (`experiments/prereg/*.yaml`, hashed into the ledger before runs): hypotheses with predictions and
falsifiers, metrics, thresholds, holdout split, budget, stopping rule.

**Controls in every batch** (assay-style QC):
- Positive: a known stable compound (and a known superconductor in the chemical class).
- Negative: an ordinary metal/insulator and a deliberately destabilized structure.
- If controls fail, the batch is **invalid** and cannot be interpreted.

**Baselines under matched conditions** (same candidate pool, same compute budget):
- B0: random substitutions.
- B1: fixed chemical heuristic (isovalent, radius-matched), no learning.
- Agentic: Planner + Analyst + strategy memory.

**Leakage control:** split holdouts by chemical family/prototype, not randomly. CHGNet saw MP data. Never evaluate the
Tc proxy on families in its training set.

**Free ground-truth trick (calibration set):** many generated substitutions will coincide with compounds that already exist
in MP/OQMD (StructureMatcher). Those have DFT hull values, so hide them from the hull reference, score them with the pipeline, and
compare. This yields precision/recall of the stability filter vs DFT with no extra DFT runs. Same matches double as the
novelty flag (matched -> "known", not novel).

## 7. Metrics
- Stability filter: precision, recall, calibration (CHGNet-stable vs DFT-stable).
- Ranking (Arm A): AUROC and enrichment / hit-rate@k of held-out superconductors vs decoys; Spearman for Tc proxy.
- Agentic learning: hit rate in iteration 1 vs N vs baselines at equal budget.
- Funnel: candidates in -> stable -> metallic -> promoted, and **recall retained** relative to ground truth.
- **Cost per validated hit at fixed recall** (headline). Bootstrap confidence intervals.

## 8. Acceleration accounting (report only what is measured)
Time a real DFT/coarse-DFT sample on our own hardware as the denominator. Report: candidates/hour per tier, DFT calls
avoided and recall kept, latency from "result available" to "next batch proposed", and **end-to-end wall-clock including
LLM overhead**. State what would be needed to approach 10x (bigger pool, GPU batching, better surrogate).

## 9. Reporting rules
Every number in the report traces to a ledger run ID. Label agent hypotheses. State limitations: surrogate error,
OOD risk, conventional-only scope, no experimental validation. State the validation still needed: T3 DFT/e-ph,
then synthesis.

## 10. References (confirm each before citing; do not cite from memory)
- Deng et al., CHGNet, Nature Machine Intelligence (2023).
- Jain et al., Materials Project, APL Materials (2013).
- Choudhary et al., JARVIS, npj Computational Materials (2020); Choudhary and Garrity, BCS-inspired superconductor
  screening, npj Computational Materials (2022).
- Sommer et al., 3DSC, Scientific Data (2023).
- Allen and Dynes, PRB (1975); McMillan, Phys. Rev. (1968).
- Drozdov et al., H3S (Nature 2015) and LaH10 (Nature 2019) high-pressure reports.
- Royal Swedish Academy of Sciences, 2024 Nobel Prize in Chemistry (brief's inspiration).
