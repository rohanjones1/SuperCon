from lab.tools_api import lookup_screening

DFT_CRITERION = 0.05  # MP/DFT ground-truth stability criterion, eV/atom


def review_screening(material_ids: list) -> dict:
    """Retrospective review: CHGNet triage decision vs MP DFT ground truth (in-sample)."""
    out = lookup_screening(material_ids, include_ground_truth=True)
    for r in out.get("results", []):
        h = r.get("mp_dft_hull_ev_per_atom")
        if h is None or "screening_decision" not in r:
            continue
        stable = h <= DFT_CRITERION
        retained = r["screening_decision"] == "RETAIN"
        r["dft_stable_at_0.05"] = stable
        r["outcome"] = (
            ("retained_stable" if stable else "retained_unstable")
            if retained
            else ("missed_stable" if stable else "correctly_deprioritized")
        )
    out["review_note"] = ("Retrospective in-sample review against MP DFT ground truth; "
                          "not available for novel candidates.")
    return out