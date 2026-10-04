# Discovery report: run 8

## Question
Can the calibrated CHGNet screening layer reduce the downstream expensive-validation pool while retaining a high fraction of promising materials? (Stability gating is the bottleneck before expensive validation.)

## Hypothesis (agent_generated, registered before the run)
AGENT HYPOTHESIS (pre-experiment): The calibrated CHGNet energy-above-hull stability gate will remove a modest fraction of the cached pool while retaining most structures that would pass the Materials Project hull stability criterion, because calibration aligns the gate to MP-referenced thresholds.
- Predicted pool reduction: minor; predicted recall of MP-stable: high; frozen thresholds: {"high_recall_min": 0.8, "high_recall_min_source": "reports/stability_policy.json:recall_target", "substantial_reduction_max_retained_fraction": 0.5}

## Alternatives
- composition_proxy (T1): runnable_in_this_lab=True
- chgnet_triage (T1): runnable_in_this_lab=True
- dft_validation (T3): runnable_in_this_lab=False
- Planner's reasoning (agent_generated): composition_proxy: measures family-held-out AUROC/enrichment for a composition Tc proxy (reports/arm_a_results_run1.json), not energy-above-hull gate retention or removal on the cached pool; wrong construct for hypothesis 10. dft_validation: runnable_in_this_lab is false (menu note: not available; script generation only); cannot be scheduled in this lab.

## Experiment
- Selected: chgnet_triage (spec_id 11); pool calibration_run_3; max_candidates (upper bound) 395
- Why / expected learning (agent_generated): If applying the deployed screening cutoff to the cached pool shows a modest rejected fraction while most MP-hull-stable references remain accepted, the next decision can treat the gate as a cheap pre-filter before higher-fidelity tests. If recall against MP-referenced stability is clearly worse than the in-sample policy metrics suggest, the next decision should defer pool-wide filtering until held-out calibration or a smaller audit batch. If precision is poor (many false rejects among known-stable controls), the next decision should revert to hull checks without aggressive triage.
- Stop rule (agent_generated): Stop after one batch completes with passing positive and negative controls and ledger-recorded triage outcomes for all candidates in scope; do not extend the batch to chase better in-sample metrics. Abort and record failure if controls fail or CHGNet relaxation errors exceed the batch invalidation policy.

## Result
- Screened 39 of 39; RETAINED BY CALIBRATED SCREENING 15; DEPRIORITIZED 24; validation queue 15
- Controls: passed (positive_hull_0, negative_hull_cutoff_plus_1, determinism_rescreen, frozen_convention)
- Runtime: 2579.380 ms (cache lookup, not CHGNet compute time)
- Cutoff 0.0585322711221589 eV/atom, frozen convention corrected, calibration run 3

## Retrospective evidence (in-sample; NOT prospective performance)
- TP 11, FP 4, FN 3, TN 21; precision 0.733, recall 0.786 (MP hull <= 0.05 eV/atom, column mp_energy_above_hull_summary)
- In-sample: the screening cutoff was calibrated on this same pool, so retrospective recall/precision here are a consistency check, not evidence of generalization.

## Analyst conclusion
Verdict (computed): **not_supported**; observed {"pool_reduction": "substantial", "recall_of_mp_stable": "not_high"} vs predicted minor/high

## Learning
- (agent_generated) CHGNet triage retained 15 of 39 candidates (retained_fraction ≈ 0.385), classifying pool reduction as substantial rather than minor, with retrospective MP recall 0.786 and precision 0.733 (tp=11, fp=4, fn=3, tn=21) on n_with_ground_truth=39.

## Next decision
- Proposed (agent_generated): narrow_downstream_to_survivors (deterministic signal narrow_downstream_to_survivors; match True)
- Proposed rule change: none (status: proposed_not_applied; applied: False)
- Next experiment: held_out_chgnet_validation, status blocked: requires held-out CHGNet computation not available in this lab

## Auditor
VETO (audit_id 10): critical checks failed: scientific_boundary_language, retrospective_metrics_without_in_sample_label:analyst_what_was_learned
- warning: retrospective counts {'tp': 11, 'fp': 4, 'fn': 3, 'tn': 21} differ from stability_policy.json counts {'tp': 12, 'fp': 4, 'fn': 3, 'tn': 20}; ground-truth column (mp_energy_above_hull_summary) may differ from the one used for calibration (open assumption)

## Human gate
pending

## Validation boundary
CHGNet triage is not DFT validation. RETAIN means worth computing DFT stability; DEPRIORITIZE does not mean unstable. The only DFT-derived truth here is the retrospective MP label, used in-sample. Held-out validation of the calibrated cutoff remains required.

## Traceability
- hypothesis_id 10; spec_id 11; run_id 8; evaluation_run_id 9; audit_id 10; approval_id pending; plan_id pending; policy calibration run_id 3
- Artifacts: experiments/runs/run_8*.json; ledger data\ledger.sqlite

_CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data._
