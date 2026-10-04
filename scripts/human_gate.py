"""Human approval gate for the Analyst's proposed next decision (interactive; human only).

    uv run python scripts/human_gate.py --run-id 6

Shows the latest audit and the proposed decision, then asks the human to type APPROVE or REJECT.
Agents are never given this tool. The record is write-once (one human decision per run).
"""
from __future__ import annotations

import argparse
import json
import sys

from lab import governance as gov
from lab import loop_tools as lt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    args = ap.parse_args()

    nd_path = lt.RUNS_DIR / f"run_{args.run_id}_next_decision.json"
    if not nd_path.exists():
        print("STOP: run the Analyst first (no next decision for this run).")
        sys.exit(1)
    audit = gov._latest_audit(args.run_id)
    if audit is None:  # deterministic audit, no claims text (e.g. runs made before the Auditor existed)
        audit = gov.audit_iteration(args.run_id)
        if "error" in audit:
            print(json.dumps(audit, indent=2))
            sys.exit(1)
    nd = json.loads(nd_path.read_text(encoding="utf-8"))

    print(f"Run {args.run_id}  audit_id {audit['audit_id']}: {audit['audit_status']}  ({audit['reason']})")
    for w in audit["warnings"]:
        print(f"  warning: {w}")
    print(f"Proposed next decision (agent_generated): {nd['next_decision']}")
    print(f"  learned:   {nd['what_was_learned']}")
    print(f"  rationale: {nd['rationale']}")
    print(f"Proposed rule change: {nd['proposed_rule_change']}")
    if audit["veto"]:
        print("Audit VETO: only REJECT is accepted.")

    choice = input("Type APPROVE or REJECT: ").strip().upper()
    rule_ok = False
    if choice == "APPROVE" and (nd.get("proposed_rule_change") or "none").lower() != "none":
        rule_ok = input("Also approve the proposed rule change? (it stays unapplied) [y/N]: ").strip().lower() == "y"
    approver = input("Your name/initials: ").strip()
    note = input("Note (optional): ").strip()

    out = gov.record_human_decision(args.run_id, audit["audit_id"], choice, approver,
                                    approve_rule_change=rule_ok, note=note, channel="cli_interactive")
    print(json.dumps(out, indent=2))
    sys.exit(1 if "error" in out else 0)


if __name__ == "__main__":
    main()
