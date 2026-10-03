# Decision log
Format: ID | status | decision / default | rationale | owner. Claude Code: append open questions here instead of guessing.

| ID | Status | Decision / default | Rationale |
|---|---|---|---|
| D9 | accepted | MP key env var is `MAT_PROJECT_API` (pass explicitly to mp-api) | User's setup; mp-api default is MP_API_KEY |
| D1 | **ACCEPTED** | Material class: conventional ambient-pressure superconductors; default family = borides/carbides/silicides/simple intermetallics | Valid for DFT/phonon methods; data exists; sets seeds, Tc-proxy training set, holdout design |
| D2 | default | Stability = relaxed CHGNet energy -> E_above_hull vs MP competing phases; threshold 0.05 eV/atom (pre-register) | Raw `e < 0` is meaningless |
| D3 | default | T2 = no real DFT unless time allows; calibration via candidates matching existing MP/OQMD entries | Free ground truth without DFT cost |
| D4 | default | Tc proxy is a **stretch** ranking signal only; cut at hour 6 if behind | Weak generalization; do not overinvest |
| D5 | default | Omnigent: local open-source, not Databricks-managed, unless smoke test favors managed | Fewer moving parts |
| D6 | PENDING | Two model vendors available for Auditor? | Cross-vendor review needs two credentials |
| D7 | default | Python 3.12, `uv`, SQLite ledger | Omnigent needs 3.12+ |
| D8 | default | Headline metric = cost per validated hit at fixed recall vs B0/B1 at matched budget | Measurable, honest |

| D10 | **ACCEPTED** | JARVIS supercon_3d Tc values are **computed reference labels** (DFPT + Allen-Dynes). Column name `ref_tc`. Do NOT call them T1 experimental claims. | Corrected before any result is logged. |
| D11 | **ACCEPTED** | Benchmark negatives = JARVIS low-ref_tc entries. Materials Project is NOT used for decoys; MP is used in M3 only for hull / competing phases. | Keeps sources clean and avoids MP in-distribution leakage for novelty. |
| D12 | **ACCEPTED** | Simple composition-based Tc proxy (Allen-Dynes with empirical lambda from composition features) promoted from stretch to **core**; phonon check is the first cut-line item in PLAN_16H.md. | Proxy needed for B1 baseline and Hypothesis agent scoring. Phonon is expensive; demote if behind schedule. |
| D13 | **ACCEPTED** | Split unit = `group_id` (union-find: rows sharing `prototype_key` OR `formula` merged into same component). `assert_no_leakage` checks both. | Prevents composition leakage from polymorphs with different spacegroups. |
| D14 | **ACCEPTED** | Milestone order changed: M3 = Tc proxy + Arm A benchmark (AUROC vs B0/B1 on holdout); M4 = stability engine (CHGNet relax + E_above_hull). | Proxy result is faster to get and directly tests H1/H2 pre-registered hypotheses. |
| D15 | **ACCEPTED** | Primary analysis = grouped 5-fold CV with cluster bootstrap over group_id; fixed split (seed 42, ~35.5% holdout) is descriptive only. Split overshot target before any model was trained. | Grouped folds use all data for training and evaluation, avoid leakage, and give better CI estimates than a single held-out set. |
| D16 | **ACCEPTED** | Arm A result: H1 not supported (proxy AUROC CI includes 0.5), H2 inconclusive (point estimate favors proxy but paired-diff CI includes 0). Exploratory 10K threshold showed higher AUROC — hypothesis-generating only, not a pre-registered claim. Plan: Analyst agent reads these verdicts and proposes the next experiment (e.g., retrain with 10K threshold, add structure features, or pivot to stability-first funnel). No human-chosen follow-up experiment until Analyst output is reviewed. | Arm A verdicts are pre-registered; exploratory lead is clearly labeled T0. Analyst agent change-of-direction maintains auditability. |

## Open questions (fill in answers from reading the Omnigent repo)
- Parallel sub-agents? Result handoff format?
- Custom policy handler for approval gate on `release_candidate`?
- Structured I/O limits for function tools?
- Headless/scripted `omnigent run` and transcript capture?
- chgnet/pymatgen/torch compatibility on Python 3.12?
- MP-compatible energy corrections for CHGNet vs MP hull: confirmed convention?
- JARVIS / 3DSC field names for Tc, pressure, structure? **RESOLVED: supercon_3d fields = Tc, lamb, wlog, press, stability, atoms, jid, cfid, a2F, a2F_original_x/y**
