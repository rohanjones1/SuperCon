# Discovery iteration: run 8

**Scientific question:** Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool while retaining a high fraction of promising materials?

**Hypothesis (agent_generated, hypothesis_id 10):** AGENT HYPOTHESIS (pre-experiment): The calibrated CHGNet energy-above-hull stability gate will remove a modest fraction of the cached pool while retaining most structures that would pass the Materials Project hull stability criterion, because calibration aligns the gate to MP-referenced thresholds.
- Predicted pool reduction: minor; observed: substantial
- Predicted recall of MP-stable: high; observed: not_high
- Verdict (computed): **not_supported**

**Experiment chosen (spec_id 11):** chgnet_triage

| Measure | Value |
|---|---|
| Candidate pool | 39 (screened 39) |
| RETAINED BY CALIBRATED SCREENING | 15 |
| DEPRIORITIZED | 24 |
| Validation queue size | 15 |
| Retained fraction | 0.385 |
| Controls passed | True |

**Retrospective MP metrics (in-sample, not prospective performance):** precision 0.733, recall 0.786 (TP 11, FP 4, FN 3, TN 21). In-sample: the screening cutoff was calibrated on this same pool, so retrospective recall/precision here are a consistency check, not evidence of generalization.

**What was learned (agent_generated):** CHGNet triage retained 15 of 39 candidates (retained_fraction ≈ 0.385), classifying pool reduction as substantial rather than minor, with retrospective MP recall 0.786 and precision 0.733 (tp=11, fp=4, fn=3, tn=21) on n_with_ground_truth=39.

**Next decision (agent_generated):** narrow_downstream_to_survivors (deterministic signal: narrow_downstream_to_survivors; match: True)
- Rationale: Per spec_stop_rule, one batch with passing controls (controls_passed=True) and full-scope triage (n_screened=39) is sufficient to stop without extending the batch. Per spec_expected_learning, observed recall_of_mp_stable was not_high and pool_reduction was substantial versus the predicted minor/high pattern, so we do not treat the gate as validated for pool-wide pre-filtering; we follow deterministic_decision_signal and narrow downstream work to the 15 survivors in the validation queue rather than scaling triage across the pool.
- Proposed rule change (proposed_not_applied): none
- Required next validation: Held-out evaluation of the calibrated cutoff on compounds not used for calibration (requires a CHGNet compute tool; not available in this loop).

Traceability: run_id 8, evaluation_run_id 9, spec_id 11, hypothesis_id 10, policy calibration run 3, 39 candidate IDs in experiments/runs/run_8.json.

_CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data._
