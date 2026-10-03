"""scripts/lock_prereg.py — Fill sha256, write prereg_001.yaml, record in ledger.

Refuses to overwrite an existing prereg_001.yaml.
Usage:
    uv run python scripts/lock_prereg.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: uv pip install pyyaml")
    sys.exit(1)

from lab.ledger import init_db, record_prereg

DRAFT = ROOT / "experiments" / "prereg" / "draft_001.yaml"
LOCKED = ROOT / "experiments" / "prereg" / "prereg_001.yaml"
SEEDS_CSV = ROOT / "data" / "processed" / "seeds.csv"
LEDGER = ROOT / "data" / "ledger.sqlite"

# ── Guard: refuse to overwrite ───────────────────────────────────────────────
if LOCKED.exists():
    print(f"ERROR: {LOCKED} already exists.")
    print("Refusing to overwrite a locked pre-registration.")
    sys.exit(1)

if not DRAFT.exists():
    print(f"ERROR: {DRAFT} not found.")
    sys.exit(1)

if not SEEDS_CSV.exists():
    print(f"ERROR: {SEEDS_CSV} not found. Run build_dataset.py first.")
    sys.exit(1)

# ── Compute seeds.csv SHA256 ──────────────────────────────────────────────────
sha256 = hashlib.sha256(SEEDS_CSV.read_bytes()).hexdigest()
print(f"seeds.csv SHA256  : {sha256}")

# ── Load draft, fill sha256 ───────────────────────────────────────────────────
content = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
content["dataset"]["sha256"] = sha256

# ── Write prereg_001.yaml (pyyaml strips comments — that is expected) ─────────
locked_text = yaml.dump(
    content,
    sort_keys=False,
    allow_unicode=True,
    default_flow_style=False,
)
LOCKED.write_text(locked_text, encoding="utf-8")
print(f"Written           : {LOCKED}")

# ── Record in ledger ──────────────────────────────────────────────────────────
init_db(LEDGER)
ledger_hash = record_prereg(content, path=LEDGER)
print(f"Ledger prereg hash: {ledger_hash}")
print("Pre-registration locked successfully.")
