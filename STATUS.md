# Status
- Current milestone: **M4a Amendment A applied** (pending pytest + --select-only + full run)
- M0-M3 all PASS. M3 Arm A: H1 not supported, H2 inconclusive (D16).
- Amendment A (written before any metric seen): GGA_GGA+U only, no compatible_only toggle,
  ground truth from thermo doc, uncorrected = same entries.uncorrected_energy_per_atom.
- Bug fixed: Element-keyed dict JSON crash → {str(k): v for k, v in e.composition.items()}.
- New cache files: {chemsys}_GGA_GGA+U.json (entries), {mat_id}_GGA_GGA+U.json (ground truth).
  Old _corr/_uncorr cache files NOT reused.
- Partial CSV: reports/stability_calibration_partial.csv — appended per compound; skipped on rerun.
- Excludes compounds with no GGA_GGA+U thermo doc; prints them; records error in partial CSV.
- tests/test_stability.py: 4 tests (a/b/c: leave-one-out hull; d: Element-key JSON bug fix).
- Next: uv run pytest tests/test_stability.py -v
  then: uv run python scripts/calibrate_stability.py --select-only
  then: uv run python scripts/calibrate_stability.py
- Open: D6 (two model vendors) pending; thermo_types param availability in installed mp-api version.
