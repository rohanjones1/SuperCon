# KB 04: Architecture spec

## Agents
| Agent | Decision it owns | Tools (scoped) | Reads | Writes |
|---|---|---|---|---|
| PI (supervisor) | Sequence, budget, when to stop | delegation only | ledger summary | iteration log |
| Evidence | What is known / gaps | MP, JARVIS, 3DSC lookups; OpenAlex/MCP search | prereg | evidence cards (with citations), seed + holdout proposal |
| Hypothesis | What to test next | substitute_batch, novelty_check | evidence cards, strategy rules | hypotheses (H-ids, `agent_generated`), candidate batch |
| Planner | Which test, at what cost | cost_model, budget_status | hypotheses, results, uncertainty | experiment spec |
| Runner | Execute exactly the spec | run_batch (relax, hull, controls, tc_proxy, phonon*) | experiment spec | results + control outcomes + run IDs |
| Analyst | What the result means | ledger (read), metrics | results, prereg predictions | verdicts, surprises, strategy-rule proposals |
| Auditor (other vendor) | Is it trustworthy | ledger (read), audit_checklist | everything | pass / veto + reasons |
| Safety/Approval | May this proceed | release_candidate (gated by policy) | audit result | approvals, HPC script release |
(*stretch)

Rule: only Runner has compute tools; only Analyst/Auditor read raw results; only the approval gate can release.

## Iteration loop
1. Human sets objective and locks **prereg** (hashed).
2. Evidence -> evidence cards, seed set, holdout split (split produced by a deterministic tool).
3. Hypothesis -> labeled hypotheses + candidate batch (novelty-filtered).
4. Planner -> picks one of the competing tests under budget; logs expected learning, cost, feasibility, rationale.
5. Runner -> executes batch with controls. Failed controls -> batch invalid, loop returns to step 4.
6. Analyst -> verdict per hypothesis (supported / refuted / inconclusive vs pre-stated prediction), surprises,
   fidelity disagreements, proposed rule updates.
7. Auditor -> checks traceability, leakage, controls, counter-explanations. May veto.
8. Safety/Approval -> human approves tier promotion and HPC script release.
9. Strategy memory updates; PI checks budget and loops, or finalizes the report.

## Planner test menu (at least two competing tests per decision)
- **A:** relax + E_hull (cheap, baseline).
- **B:** A + phonon dynamical-stability check (costlier, more informative where A is uncertain).
- **C:** Tc-proxy ranking (cheap, weak signal).
- **D:** coarse DFT (expensive, highest tier).
Scoring: expected learning (model uncertainty, fidelity disagreement, region coverage) / cost, gated by feasibility. Log all scores.

## Strategy rules (memory)
Each rule: id, statement (e.g. "substitutions with radius mismatch > X% rarely hull-stable"), supporting run IDs, support count,
confidence, status (active / under_review / retracted). **Surprises** (prediction violated, or T1/T2 disagree) set
`under_review` and trigger a targeted test next iteration.

## Ledger (append-only SQLite). Entities
- `prereg`: id, content, hash, timestamp.
- `seeds`: source, source_id, formula, structure_hash, known_tc, pressure, family, split (train/holdout).
- `candidates`: id, parent_id, hypothesis_id, structure_hash, formula, generator, novelty_flag, batch_id.
- `hypotheses`: id, text, prediction, falsifier, agent_generated=true, status.
- `experiments`: id, spec (JSON), planner_scores (JSON), chosen_test, budget_before/after.
- `runs`: id, experiment_id, tool, tool_version, model_checkpoint, seed, hardware, wall_ms.
- `results`: id, run_id, candidate_id, tier, metric, value, uncertainty.
- `controls`: run_id, control_id, expected, observed, passed.
- `verdicts`: hypothesis_id, experiment_id, verdict, evidence_run_ids.
- `strategy_rules`: as above, with history of status changes.
- `approvals`: id, action, requested_by, approver, decision, timestamp.
- `audits`: id, scope, outcome, reasons.
- `timing`: stage, start, end (for acceleration accounting, including LLM overhead).

## Handoff contracts (minimal JSON; pass IDs, not structures)
```json
// Hypothesis -> Planner
{"batch_id":"B003","hypothesis_ids":["H07","H08"],"n_candidates":120}
// Planner -> Runner
{"experiment_id":"E012","batch_id":"B003","test":"B","params":{"hull_threshold":0.05},
 "est_cost_s":900,"expected_learning":"resolve T1 uncertainty in boride region","stop_if":"controls_fail"}
// Runner -> Analyst
{"experiment_id":"E012","run_ids":["R031"],"controls_passed":true,"n_scored":118,"n_failed":2}
// Analyst -> Hypothesis (via strategy rules)
{"rule_updates":[{"id":"S04","status":"under_review","reason":"surprise: C017 stable despite rule"}]}
```

## Policies (enforce boundaries in tool permissions, not prompts)
- Budget: `cost_budget` (LLM spend) plus an internal compute-second budget in the ledger.
- `max_tool_calls_per_session` sized for batch-level tools.
- Approval required: tier promotion (T1 -> T2/T3), HPC script release, any "promising" label in the final report.
- Ask before shell/file writes outside the workspace.
- Runner is the only agent with compute tools; ledger access is read-only for Analyst/Auditor.

## Final report (generated from ledger)
Question; pre-registration; agent handoff trace; controls and baselines; results with CIs; verdicts; strategy changes;
measured acceleration; candidate table with tier and uncertainty; limitations; required next validation (T3, then T4); citations.
