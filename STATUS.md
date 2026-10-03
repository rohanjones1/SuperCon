# Status
- Current milestone: **M4a updated** (pending pytest tests/test_stability.py + --select-only run)
- M0-M3 all PASS. M3 Arm A: H1 not supported, H2 inconclusive, 10K exploratory hypothesis-only (D16).
- M4a: addendum_stability_001.yaml locked before running (user-edited version, no further changes).
- lab/stability.py: leave-one-out hull — reference PD excludes target's material_id; max(0, E_chgnet - hull_epa).
- lab/relax.py: CHGNet StructOptimizer fmax=0.1, steps=500; error field on failure.
- scripts/calibrate_stability.py: bands=[0.00,0.05)/14, [0.05,0.10)/8, [0.10,0.20)/9, [0.20,0.30]/9.
  Selection: sha256(mat_id) ordering, global 2-per-chemsys cap, no theoretical filter.
  --select-only flag; Wilson 95% CI for precision/recall; INDETERMINATE recall if n_stable<10.
  Prints frozen convention (lower hull MAE); records T1 results to ledger.
- tests/test_stability.py: 3 synthetic PDEntry tests (no network): loso changes result, below hull=0, above hull=correct.
- Next: uv run pytest tests/test_stability.py -v; then uv run python scripts/calibrate_stability.py --select-only.
- After --select-only confirmed: uv run python scripts/calibrate_stability.py (full run with CHGNet).
- Open: D6 (two model vendors) pending; CHGNet/torch version check.
