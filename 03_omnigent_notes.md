# KB 03: Omnigent notes (from the project README; alpha software)

## What it is
Open-source **meta-harness**: a common orchestration layer over Claude Code, Codex, Cursor, OpenCode, Hermes, Pi, and
custom agents. Mix harnesses in one session, enforce policies/sandboxing, share and co-drive sessions live.
Apache 2.0, alpha. Managed beta exists on Databricks; open source runs locally with your own keys.

## Setup facts
- Requires **Python 3.12+**, `uv`, `git`; Node 22+ for coding-harness CLIs; `tmux` for native wrappers;
  `bubblewrap` on Linux. Windows is degraded (SDK harnesses and web UI only; use WSL for the rest).
- Install: `uv tool install omnigent` (or the repo install script). Databricks model provider: extra `databricks`.
- Start: `omnigent` (terminal + web UI at `http://localhost:6767`), `omnigent start`, `omnigent setup` (credentials/models).
- Run an agent file: `omnigent run path/to/agent.yaml` (add `--harness <h>` to switch).
- Example agents in the repo: `examples/polly/` (supervisor + parallel sub-agents + cross-vendor review),
  `examples/debby/` (two-model debate), `examples/deep-research/` (cited, cross-checked research with an MCP search server,
  the simplest to copy).
- Sandboxes: Databricks, Modal, Daytona, E2B, Kubernetes and others. Compute dependencies must exist in the sandbox image.
- Other features: shareable sessions, `omnigent attach <id>` (co-drive), `--fork`, **Automations** (scheduled runs).

## Agent YAML (shape per README; confirm against docs/AGENT_YAML_SPEC.md)
```yaml
name: my_agent
prompt: ...
executor: { harness: claude-sdk }      # others: codex, cursor, opencode, pi, openai-agents, ...
tools:
  some_fn:   { type: function, callable: package.module.fn }   # schema from signature
  some_mcp:  { type: mcp, url: https://... }
  sub_agent: { type: agent, prompt: ..., tools: { some_fn: inherit } }
```

## Policies (shape per README; confirm against docs/POLICIES.md)
Levels: server-wide, per-agent, per-session (stricter session rules checked first). Actions: allow / block / pause for approval.
Builtins named in the README: `safety.ask_on_os_tools` (ask before shell/file writes), `safety.max_tool_calls_per_session`,
`cost.cost_budget` (hard cap + soft ask thresholds).

## Mapping to our design
| Need | Omnigent feature |
|---|---|
| Supervisor / PI | top-level agent with `type: agent` sub-agents |
| Specialists | sub-agents with scoped `tools` |
| Deterministic science | `type: function` tools (or one local MCP server) wrapping `lab/tools_api.py` |
| Tool permissions per role | per-agent tool lists + `inherit` discipline |
| Budget / call caps | `cost_budget`, `max_tool_calls_per_session` |
| Human approval of tier promotion | approval policy (**verify** whether a custom function handler can gate our `release_candidate` tool) |
| Independent auditor | sub-agent on a different `harness`/vendor (Polly pattern) |
| Literature with citations | copy `examples/deep-research/` MCP search pattern |
| Live human oversight in demo | shared session / approval cards in the web UI |

## Open items to verify by reading the repo (record answers in DECISIONS.md)
1. Can sub-agents run in parallel, and how are results handed back to the supervisor?
2. Can a custom Python policy handler be attached per tool (needed for the approval gate)?
3. How do function tools receive/return structured data (types, size limits)?
4. Can we run headless/scripted (`omnigent run` non-interactive) so the benchmark loop is reproducible, and capture a transcript?
5. Which harness/model credentials are available to us (need two different vendors for the Auditor)?
6. Is the Databricks-managed route worth it, or stay local/open-source? (Default: local open-source.)

## Risk plan
Alpha means bugs. Keep `lab/` standalone with a CLI (`python -m lab.run_batch ...`) so results exist even if orchestration
breaks. Hour-0 smoke test: a trivial agent calling one function tool must work before anything else is built.
