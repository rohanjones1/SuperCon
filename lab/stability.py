"""lab/stability.py — E_above_hull for a candidate structure (leave-one-out).

Hull definition (addendum_stability_001.yaml):
    Reference PhaseDiagram = all MP entries for the chemical system with the
    target's own material_id EXCLUDED (caller must pre-filter).
    Other polymorphs of the same composition remain in the reference.

    E_hull_chgnet = max(0, E_chgnet_per_atom
                           - hull_energy_per_atom(reference PD, target composition))

    Two energy conventions — determined by which entries the caller passes:

    Corrected reference  (compatible_only=True from mpr.get_entries_in_chemsys):
        Reference phases carry MP2020 corrections; CHGNet energy is inserted
        without corrections. For binary metal borides/carbides/silicides (no O),
        corrections are typically near zero so the bias is small.

    Uncorrected reference  (compatible_only=False):
        Reference phases use raw GGA energies, consistent with CHGNet's
        MPtrj training convention.

    Both are exercised in calibrate_stability.py. The convention with the
    lower hull MAE vs MP energy_above_hull is frozen for all later work and
    recorded in reports/stability_calibration.json.
"""
from __future__ import annotations

from pymatgen.analysis.phase_diagram import PhaseDiagram
from pymatgen.core import Structure


def e_above_hull(
    chgnet_energy_per_atom: float,
    structure: Structure,
    reference_entries: list,
) -> float:
    """
    Compute leave-one-out E_above_hull for a candidate using CHGNet energy.

    Parameters
    ----------
    chgnet_energy_per_atom : float
        CHGNet total energy per atom in eV/atom (from relax_structure()).
    structure : Structure
        Relaxed pymatgen Structure (only .composition is used).
    reference_entries : list of PDEntry / ComputedEntry
        MP entries for the chemical system with the target's own material_id
        already removed by the caller. Other polymorphs of the same
        composition stay in.

    Returns
    -------
    float : E_above_hull in eV/atom, clipped to >= 0.
    """
    phase_diagram = PhaseDiagram(reference_entries)
    hull_epa = phase_diagram.get_hull_energy_per_atom(structure.composition)
    return max(0.0, chgnet_energy_per_atom - hull_epa)
