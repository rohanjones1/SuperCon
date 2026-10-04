"""lab/claims.py — Deterministic scientific-boundary language scan (stdlib only).

Used by the Analyst's record_next_decision (prevention) and by the Auditor (lab.governance).
A pattern match that contains, or is preceded within 12 characters by, a negation is ignored,
so caveats such as "not DFT validation" or "DEPRIORITIZE does not mean unstable" pass.
"""
from __future__ import annotations

import re

FORBIDDEN = {
    "chgnet_described_as_dft": [
        r"dft[- ]validat\w*", r"validated (?:by|with|using) dft", r"dft[- ]confirmed",
        r"confirmed (?:by|with) dft", r"chgnet\W+(?:is|as|equals)\W+(?:a\W+)?dft",
    ],
    "retain_described_as_proven_stable": [
        r"\b(?:proven|confirmed|guaranteed|verified) stable\b", r"\bchgnet[- ]stable\b",
        r"retain\w*\b[^.\n]{0,40}\b(?:are|is) (?:thermodynamically )?stable\b",
        r"chgnet\W+(?:identif\w*|found|discover\w*|prov\w*|confirm\w*)\W+(?:\w+\W+){0,2}stab\w*",
        r"\bprov(?:e|es|ed|en|ing)\s+(?:their\s+|its\s+)?stability\b",
    ],
    "deprioritize_described_as_unstable": [
        r"deprioriti[sz]\w*\b[^.\n]{0,40}\b(?:are|is|as|means|=)\s+(?:thermodynamically\s+)?unstable\b",
    ],
    "policy_presented_as_generalization": [
        r"generali[sz]\w*\s+(?:to|on)\s+(?:unseen|new|novel|held[- ]out)",
        r"prospective (?:performance|recall|precision)",
        r"(?:recall|precision)\b[^.\n]{0,40}\b(?:on|for|to)\s+(?:unseen|new|novel|held[- ]out)",
    ],
}
NEGATION = re.compile(r"\bnot\b|n't\b|\bnever\b|\bno\b|\bwithout\b|\bunverified\b", re.I)
# Retrospective metric claims: precision/recall with a 2+ decimal value, or confusion counts.
METRIC_CLAIM = re.compile(
    r"(?i:precision|recall)[^.\n]{0,40}?\b0\.\d{2,}"
    r"|\b\d+\s*(?:TP|FP|FN|TN)\b|\b(?:TP|FP|FN|TN)\s*[=:]?\s*\d+",
)
IN_SAMPLE = re.compile(r"in[- ]sample", re.I)


def scan_claims(text: str) -> list[str]:
    """Return failed boundary-check names for free text (forbidden claims, unlabeled metrics)."""
    text = text or ""
    failed = []
    for name, patterns in FORBIDDEN.items():
        for pat in patterns:
            hit = any(not NEGATION.search(m.group(0)) and not NEGATION.search(text[max(0, m.start() - 12):m.start()])
                      for m in re.finditer(pat, text, re.I))
            if hit:
                failed.append(name)
                break
    if METRIC_CLAIM.search(text) and not IN_SAMPLE.search(text):
        failed.append("retrospective_metrics_without_in_sample_label")
    return failed
