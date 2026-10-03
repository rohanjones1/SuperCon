"""lab/relax.py — CHGNet structure relaxation wrapper.

Fixed parameters (not tuned): fmax=0.1 eV/Å, max_steps=500.

CHGNet trajectory convention:
    StructOptimizer.relax() returns {"final_structure": Structure,
    "trajectory": TrajectoryObserver}. trajectory.energies is a list of
    total energies in eV (summed over all atoms, one per ionic step).
    energy_per_atom = trajectory.energies[-1] / len(relaxed_structure).
"""
from __future__ import annotations

import time
from typing import Any

from pymatgen.core import Structure


def relax_structure(structure: Structure) -> dict[str, Any]:
    """
    Relax a pymatgen Structure with CHGNet StructOptimizer.

    CHGNet is imported lazily (it is heavy and pulls in torch).

    Returns
    -------
    dict with keys:
        relaxed_structure : Structure | None
        energy_per_atom   : float | None   eV/atom, total DFT-like (no MP corrections)
        n_steps           : int | None     number of ionic steps taken
        wall_ms           : float          wall-clock ms including import time
        error             : str | None     non-None on any failure; other values may be None
    """
    t0 = time.time()
    try:
        from chgnet.model import StructOptimizer  # lazy import

        optimizer = StructOptimizer()
        result = optimizer.relax(structure, fmax=0.1, steps=500)

        relaxed: Structure = result["final_structure"]
        trajectory = result["trajectory"]

        # trajectory.energies: list[float], total eV per ionic step
        energies = trajectory.energies
        n_steps = max(0, len(energies) - 1)  # initial state counts as step 0
        energy_per_atom = float(energies[-1]) / len(relaxed)

        return {
            "relaxed_structure": relaxed,
            "energy_per_atom": energy_per_atom,
            "n_steps": n_steps,
            "wall_ms": (time.time() - t0) * 1000,
            "error": None,
        }
    except Exception as exc:
        return {
            "relaxed_structure": None,
            "energy_per_atom": None,
            "n_steps": None,
            "wall_ms": (time.time() - t0) * 1000,
            "error": str(exc),
        }
