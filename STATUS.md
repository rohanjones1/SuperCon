# Status
- Current milestone: **M5a agent-callable lookup tool (written, awaiting test run)**
- M0-M3 PASS. M3 Arm A: H1 not supported, H2 inconclusive (D16).
- M4: policy cutoff=0.058532 eV/atom (corrected convention, run 3, in-sample n=39); batch layer lab/run_batch.py (D17, D18).
- M5a: lab/tools_api.py
  - lookup_screening(material_ids) -> dict: cap 5 ids; reads chgnet_hull_corrected from
    reports/stability_calibration.csv; decisions via run_batch.screen_batch; unknown/excluded ids -> not_in_cache.
  - get_policy() -> dict: policy JSON + caveat. Both never raise; no CHGNet/mp-api/network (D19).
  - include_ground_truth=False by default (MP DFT hull hidden); top-level priority_note
    (lower validation_priority = validate first) and timing_note (cache lookup, not CHGNet time).
  - tests/test_tools_api.py: 8 tests (tmp CSV + tmp policy).
- Open: confirm mp_dft_hull column (assumed mp_energy_above_hull_summary, vs _gga) matches run-3 ground truth.
- Open: D6 (two model vendors) pending; controls/baselines/metrics not yet written.
- Next: uv run pytest tests/test_tools_api.py -v, then M5b (wrap tools in Omnigent agent YAML).
