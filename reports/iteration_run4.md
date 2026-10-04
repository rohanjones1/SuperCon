# Discovery iteration: run 4

**Scientific question:** Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool while retaining a high fraction of promising materials?

**Hypothesis (agent_generated, hypothesis_id 6):** [scripted_offline] The calibrated CHGNet stability gate will retain a substantially smaller subset of the 39-compound pool while retaining most candidates that satisfy the MP stability criterion.
- Predicted pool reduction: substantial; observed: substantial
- Predicted recall of MP-stable: high; observed: not_high
- Verdict (computed): **partially_supported**

**Experiment chosen (spec_id 7):** chgnet_triage

| Measure | Value |
|---|---|
| Candidate pool | 39 (screened 39) |
| RETAINED BY CALIBRATED SCREENING | 15 |
| DEPRIORITIZED | 24 |
| Validation queue size | 15 |
| Retained fraction | 0.385 |
| Controls passed | True |

**Retrospective MP metrics (in-sample, not prospective performance):** precision 0.733, recall 0.786 (TP 11, FP 4, FN 3, TN 21). In-sample: the screening cutoff was calibrated on this same pool, so retrospective recall/precision here are a consistency check, not evidence of generalization.

**What was learned (agent_generated):** [scripted_offline] Verdict partially_supported: 15 of 39 retained by calibrated screening (in-sample).

**Next decision (agent_generated):** narrow_downstream_to_survivors (deterministic signal: narrow_downstream_to_survivors; match: True)
- Rationale: [scripted_offline] Follows the deterministic decision signal and the spec stop rule.
- Proposed rule change (proposed_not_applied): none
- Required next validation: Held-out evaluation of the calibrated cutoff on compounds not used for calibration (requires a CHGNet compute tool; not available in this loop).

Traceability: run_id 4, evaluation_run_id 5, spec_id 7, hypothesis_id 6, policy calibration run 3, 39 candidate IDs in experiments/runs/run_4.json.

_CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data._
