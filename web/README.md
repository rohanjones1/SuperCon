# Static demo

Judge page for the SuperCon T1 screen. No Python runtime, no serverless functions, no environment variables.

## Local

`fetch` of JSON fails on `file://`. From the repo:

```bash
python -m http.server -d web 8000
```

or `npx serve web`, then open the printed URL.

**Run one discovery** reloads `./data/snapshot.json` and walks the recorded agent handoff. It does not start Omnigent, CHGNet, or DFT.

Regenerate numbers from `experiments/runs/run_<id>*.json` (tracked) plus the reports:

```bash
uv run python scripts/export_demo_snapshot.py --run-id 8
```

Commit `web/data/snapshot.json`. That file is the deploy artifact.

## Vercel

Import the GitHub repo. Framework preset: Other. Output directory `web` (also set in the repo-root `vercel.json`). Install and build are no-ops. Do not set a Python runtime or env vars. The ledger is not in the deploy.
