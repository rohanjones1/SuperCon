# Discovery iteration: run 6

**Scientific question:** Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool while retaining a high fraction of promising materials?

**Hypothesis (agent_generated, hypothesis_id 8):** AGENT HYPOTHESIS (pre-experiment): For pool calibration_run_3, applying the calibrated CHGNet energy-above-hull stability gate will remove some candidates but leave most of those that would pass the Materials Project stability criterion, because CHGNet hulls are trained on MP-like data and the gate threshold was calibrated to align with MP-stable labels.
- Predicted pool reduction: minor; observed: substantial
- Predicted recall of MP-stable: high; observed: not_high
- Verdict (computed): **not_supported**

**Experiment chosen (spec_id 9):** chgnet_triage

| Measure | Value |
|---|---|
| Candidate pool | 39 (screened 39) |
| RETAINED BY CALIBRATED SCREENING | 15 |
| DEPRIORITIZED | 24 |
| Validation queue size | 15 |
| Retained fraction | 0.385 |
| Controls passed | True |

**Retrospective MP metrics (in-sample, not prospective performance):** precision 0.733, recall 0.786 (TP 11, FP 4, FN 3, TN 21). In-sample: the screening cutoff was calibrated on this same pool, so retrospective recall/precision here are a consistency check, not evidence of generalization.

**What was learned (agent_generated):** On pool calibration_run_3 (n_pool=39), chgnet_triage retained 15/39 (retained_fraction≈0.385) versus a predicted minor reduction with high MP-stable recall; retrospective in-sample recall was 0.786 (11 TP, 3 FN) and precision 0.733 (4 FP), so the pre-experiment hypothesis was not supported despite passing controls.

**Next decision (agent_generated):** narrow_downstream_to_survivors (deterministic signal: narrow_downstream_to_survivors; match: True)
- Rationale: spec_expected_learning: observed pool reduction was substantial (not minor) and recall_of_mp_stable was not_high, so we should not treat the gate as validated for low-cost pre-filtering without tightening validation (held-out recalibration, lower cutoff, or not gating on hull alone). spec_stop_rule: one batch on calibration_run_3 with controls passing is complete; deployed-policy in-sample recall at cutoff (0.786) is below the frozen high_recall_min (0.8), so we halt gate-dependent scaling and proceed by narrowing downstream work to the 15 survivors rather than repeating chgnet_triage on this pool.
- Proposed rule change (proposed_not_applied): Do not scale hull-only gating on calibration_run_3 at the current cutoff until held-out recalibration confirms recall ≥ high_recall_min (0.8); downstream batches may rank/proxy only the retained validation_queue (15 IDs).
- Required next validation: Held-out evaluation of the calibrated cutoff on compounds not used for calibration (requires a CHGNet compute tool; not available in this loop).

Traceability: run_id 6, evaluation_run_id 7, spec_id 9, hypothesis_id 8, policy calibration run 3, 39 candidate IDs in experiments/runs/run_6.json.

_CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data._
