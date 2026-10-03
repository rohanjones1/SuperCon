# Status
- Current milestone: **M4a complete + stability policy separation done**
- M0-M3 all PASS. M3 Arm A: H1 not supported, H2 inconclusive (D16).
- M4a calibration run_id=3: frozen_convention=corrected, hull_MAE=0.0383 eV/atom.
  At MP 0.05 cutoff: recall=0.667 (below 0.80 target) → NOT USABLE AS-IS.
- Stability policy: CHGNet cutoff ≠ ground-truth 0.05 threshold (D17).
  `scripts/build_stability_policy.py` sweeps cutoffs, selects recall >= 0.80 with best precision.
  `lab/decision.py`: load_policy() + screen() → RETAIN/DEPRIORITIZE (deterministic, no LLM).
  `tests/test_decision.py`: 7 tests (all in-memory, no file I/O).
- tests/test_stability.py: 4 tests (a/b/c: leave-one-out hull; d: Element-key JSON bug).
- Open: D6 (two model vendors) pending; thermo_types param in installed mp-api version.
- Next: uv run pytest tests/test_decision.py tests/test_stability.py -v
  then: uv run python scripts/build_stability_policy.py
  then: M5 (Omnigent agent wiring).
