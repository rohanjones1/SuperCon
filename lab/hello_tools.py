import json
import pathlib

_POLICY = pathlib.Path(__file__).resolve().parent.parent / "reports" / "stability_policy.json"
CAVEAT = ("CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. "
          "Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data.")

def get_policy() -> dict:
    """Return the calibrated CHGNet screening policy (read-only)."""
    try:
        return {"policy": json.loads(_POLICY.read_text()), "tier": "T1", "caveat": CAVEAT}
    except Exception as e:
        return {"error": str(e)}