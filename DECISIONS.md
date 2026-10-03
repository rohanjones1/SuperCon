# Decision log
Format: ID | status | decision / default | rationale | owner. Claude Code: append open questions here instead of guessing.

| ID | Status | Decision / default | Rationale |
|---|---|---|---|
| D1 | **PENDING (human)** | Material class: conventional ambient-pressure superconductors; default family = borides/carbides/silicides/simple intermetallics | Valid for DFT/phonon methods; data exists; sets seeds, Tc-proxy training set, holdout design |
| D2 | default | Stability = relaxed CHGNet energy -> E_above_hull vs MP competing phases; threshold 0.05 eV/atom (pre-register) | Raw `e < 0` is meaningless |
| D3 | default | T2 = no real DFT unless time allows; calibration via candidates matching existing MP/OQMD entries | Free ground truth without DFT cost |
| D4 | default | Tc proxy is a **stretch** ranking signal only; cut at hour 6 if behind | Weak generalization; do not overinvest |
| D5 | default | Omnigent: local open-source, not Databricks-managed, unless smoke test favors managed | Fewer moving parts |
| D6 | PENDING | Two model vendors available for Auditor? | Cross-vendor review needs two credentials |
| D7 | default | Python 3.12, `uv`, SQLite ledger | Omnigent needs 3.12+ |
| D8 | default | Headline metric = cost per validated hit at fixed recall vs B0/B1 at matched budget | Measurable, honest |

## Open questions (fill in answers from reading the Omnigent repo)
- Parallel sub-agents? Result handoff format?
- Custom policy handler for approval gate on `release_candidate`?
- Structured I/O limits for function tools?
- Headless/scripted `omnigent run` and transcript capture?
- chgnet/pymatgen/torch compatibility on Python 3.12?
- MP-compatible energy corrections for CHGNet vs MP hull: confirmed convention?
- JARVIS / 3DSC field names for Tc, pressure, structure?
