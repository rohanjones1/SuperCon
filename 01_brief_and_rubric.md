# KB 01: Hackathon brief and rubric (source: Challenge 03 PDF)

## Required
- **Omnigent must orchestrate the live discovery workflow.** Show multiple specialist agents exchanging outputs,
  using tools, and **adapting their plan after an experimental result.**
- Loop to demonstrate: **Question -> Evidence -> Hypothesis -> Experiment -> Result -> Updated decision.**
- Design **at least two possible tests** and choose one using **expected learning, feasibility, and cost**.
- Give the planner a **budget** and make it choose between competing tests.
- Keep a **shared research record** so every decision can be reconstructed.
- Run independent searches/experiments **in parallel** where possible. **Let surprising results reopen earlier assumptions.**
- Pass **structured evidence, candidate IDs, experiment specs and results** between agents.
- **Human approval:** scientists set the objective and approve consequential actions. A safety agent can flag risks and
  request approval. **Enforce the boundary through tool permissions and Omnigent policies.**
- **Require citations for factual claims.** Attach source evidence or run records, label agent-generated hypotheses,
  preserve uncertainty, document controls and human approval gates, **state the validation still needed before real-world use.**

## What counts as an experiment
Simulation, computational screening, benchmark evaluation, model comparison, analysis of existing data, sensitivity or
counterfactual analysis, or any reproducible computational test **that produces evidence that changes the next decision.**
Wet-lab validation is not expected.

## Acceleration
- Do not need to prove 10x. **Identify one bottleneck and demonstrate how the lab compresses, automates, or improves it.**
- **Report the improvement actually observed** (1.5x, 3x, 5x, 10x). Strength of evidence beats size of multiplier.
- Also explain what must happen for the workflow to approach 10x at scale.

## Suggested data sources (materials)
Materials Project, NIST JARVIS (structures + computed properties); OpenAlex (literature/citations).

## Rubric
| Weight | Criterion |
|---|---|
| 30% | Omnigent orchestration |
| 25% | Breakthrough potential |
| 20% | Discovery acceleration and learning |
| 15% | Scientific rigor |
| 10% | Creativity and responsibility |

## Submission checklist
- [ ] Repository
- [ ] Agent specifications and policies
- [ ] 2-minute demo (question, agent handoffs, experiment, result, what the lab learned, what it investigates next)
- [ ] Cited evidence
- [ ] Experiment code and results
- [ ] Measured improvement
- [ ] Next experiment

## Brief's suggested agent roles (adapt freely)
Literature, Insight, Experiment planner, Experiment runner, Analysis, Knowledge graph, plus Safety agent.
