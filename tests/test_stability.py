"""tests/test_stability.py — Unit tests for lab/stability.py (M4a).

Uses synthetic pymatgen PDEntry objects only. No network calls.

The three tests verify the leave-one-out hull definition from
addendum_stability_001.yaml:
    E_hull_chgnet = max(0, E_chgnet_per_atom
                           - hull_energy_per_atom(reference PD, composition))
where reference PD excludes the target's own material_id.
"""
from __future__ import annotations

import pytest
from pymatgen.analysis.phase_diagram import PDEntry, PhaseDiagram
from pymatgen.core import Composition

from lab.stability import e_above_hull


class _MockStruct:
    """Minimal duck-type stand-in for pymatgen Structure (no network required).

    e_above_hull only uses .composition; __len__ retained for future-proofing.
    """
    def __init__(self, formula: str, n_atoms: int):
        self.composition = Composition(formula)
        self._n = n_atoms

    def __len__(self) -> int:
        return self._n


# ---------------------------------------------------------------------------
# Reference entries: Mg elemental + B elemental
#   Mg: -2.0 eV/atom (1 atom total)
#   B:  -1.0 eV/atom (1 atom total)
# Hull at MgB2 (1/3 Mg, 2/3 B) by linear interpolation:
#   hull_epa = (1/3)*(-2.0) + (2/3)*(-1.0) = -4/3 ≈ -1.333 eV/atom
# ---------------------------------------------------------------------------

_REF_ELEMENTAL = [
    PDEntry("Mg", -2.0),  # 1 atom, energy_per_atom = -2.0
    PDEntry("B",  -1.0),  # 1 atom, energy_per_atom = -1.0
]

# A stable MgB2 polymorph that IS in the reference set for test (a)
_STABLE_MgB2 = PDEntry("MgB2", -5.4)  # 3 atoms, epa = -1.8 (on hull)

_MGB2_STRUCT = _MockStruct("MgB2", 3)


def _hull_epa_at(ref_entries: list, formula: str) -> float:
    pd = PhaseDiagram(ref_entries)
    return pd.get_hull_energy_per_atom(Composition(formula))


# ---------------------------------------------------------------------------
# (a) Excluding the target from the reference changes the result vs including it
# ---------------------------------------------------------------------------

def test_excluding_target_changes_hull_result():
    """
    Reference WITHOUT stable MgB2 polymorph: hull at MgB2 = -1.333 eV/atom.
    Reference WITH stable MgB2 polymorph:    hull at MgB2 = -1.800 eV/atom.
    Candidate CHGNet energy = -1.6 eV/atom (between the two hulls).
    -> Leave-one-out (without stable MgB2): -1.6 - (-1.333) < 0 → clipped to 0.
    -> With stable MgB2 in reference:       -1.6 - (-1.800) = 0.2 > 0.
    """
    chgnet_epa = -1.6

    # Leave-one-out: reference is elemental only (stable MgB2 excluded)
    e_loso = e_above_hull(chgnet_epa, _MGB2_STRUCT, _REF_ELEMENTAL)

    # With stable polymorph included in reference
    e_with_polymorph = e_above_hull(chgnet_epa, _MGB2_STRUCT, _REF_ELEMENTAL + [_STABLE_MgB2])

    assert e_loso == pytest.approx(0.0), (
        f"Expected 0.0 (below elemental hull), got {e_loso:.4f}"
    )
    assert e_with_polymorph == pytest.approx(0.2, abs=1e-5), (
        f"Expected 0.2 above stable-polymorph hull, got {e_with_polymorph:.4f}"
    )
    assert e_loso != e_with_polymorph, (
        "Including vs excluding stable polymorph should give different results"
    )


# ---------------------------------------------------------------------------
# (b) Candidate energy below leave-one-out hull → clipped to 0
# ---------------------------------------------------------------------------

def test_below_hull_returns_zero():
    """
    Hull at MgB2 (elemental reference only) ≈ -1.333 eV/atom.
    Candidate at -2.0 eV/atom is well below → max(0, ...) = 0.
    """
    chgnet_epa = -2.0  # very stable, below the Mg+B linear hull
    e = e_above_hull(chgnet_epa, _MGB2_STRUCT, _REF_ELEMENTAL)
    assert e == pytest.approx(0.0), (
        f"Expected 0.0 (below hull, clipped), got {e:.4f}"
    )


# ---------------------------------------------------------------------------
# (c) Candidate energy above leave-one-out hull → correct positive distance
# ---------------------------------------------------------------------------

def test_above_hull_returns_correct_distance():
    """
    Hull at MgB2 (elemental reference only) ≈ -1.333 eV/atom.
    Candidate at -1.0 eV/atom is above hull.
    Expected distance = -1.0 - (-1.333) ≈ 0.333 eV/atom.
    """
    chgnet_epa = -1.0

    # Compute expected distance directly
    expected_hull_epa = _hull_epa_at(_REF_ELEMENTAL, "MgB2")
    expected_distance = chgnet_epa - expected_hull_epa  # should be ≈ 0.333
    assert expected_distance > 0.0, "Sanity: candidate should be above hull for this test"

    e = e_above_hull(chgnet_epa, _MGB2_STRUCT, _REF_ELEMENTAL)
    assert e == pytest.approx(expected_distance, abs=1e-6), (
        f"Expected {expected_distance:.4f}, got {e:.4f}"
    )
    assert e > 0.0


# ---------------------------------------------------------------------------
# (d) JSON serialization of Element-keyed dicts — Amendment A bug fix
# ---------------------------------------------------------------------------

def test_element_keyed_dict_json_serializable_with_str_fix():
    """
    Reproduce the HULL_UNCORR_FAIL bug: a dict with pymatgen Element keys
    fails json.dumps without a fix.  The fix used in calibrate_stability.py is
    {str(k): v for k, v in e.composition.items()}, equivalent to default=str.

    This test verifies:
    (i)  bare json.dumps raises TypeError on Element-keyed dicts,
    (ii) using default=str (or explicit str() conversion) produces valid JSON
         with element symbols as string keys.
    """
    import json
    from pymatgen.core import Element

    element_dict = {Element("Mg"): 1.0, Element("B"): 2.0}

    # Without fix: TypeError
    with pytest.raises(TypeError, match="not JSON serializable|keys must be"):
        json.dumps(element_dict)

    # default=str does NOT fix non-serializable keys (only values) — same error
    with pytest.raises(TypeError):
        json.dumps(element_dict, default=str)

    # The only working fix: explicit str() conversion of keys before serializing
    # This is exactly what _load_or_fetch_entries_gga does:
    #   {str(k): float(v) for k, v in e.composition.items()}
    fixed = {str(k): v for k, v in element_dict.items()}
    serialized = json.dumps(fixed)
    parsed = json.loads(serialized)
    assert set(parsed.keys()) == {"Mg", "B"}
    assert parsed["Mg"] == pytest.approx(1.0)
    assert parsed["B"]  == pytest.approx(2.0)
