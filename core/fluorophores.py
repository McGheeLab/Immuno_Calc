"""
core/fluorophores.py — Fluorophore library loader & spectral queries.

Loads fluorophore data from YAML, provides lookup functions, and
calculates spectral overlap/spillover between fluorophores.
"""

from __future__ import annotations

import math
import os
from typing import Optional

import numpy as np
import yaml

from core.models import Channel, Fluorophore


# ─── Module-level cache ──────────────────────────────────────────────────────

_fluorophore_cache: dict[str, Fluorophore] = {}
_data_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _generate_gaussian_spectrum(peak: float, fwhm: float = 40.0) -> list[float]:
    """
    Generate a synthetic Gaussian emission spectrum sampled every 5nm from 400-900nm.
    FWHM (full-width at half-maximum) defaults to 40nm which is typical for organic dyes.
    """
    wavelengths = np.arange(400, 905, 5)  # 400 to 900 inclusive, step 5
    sigma = fwhm / (2 * math.sqrt(2 * math.log(2)))
    spectrum = np.exp(-0.5 * ((wavelengths - peak) / sigma) ** 2)
    return spectrum.tolist()


def load_fluorophores(yaml_path: Optional[str] = None) -> dict[str, Fluorophore]:
    """
    Load all fluorophores from the YAML database.
    Returns a dict keyed by fluorophore name.
    Caches the result for subsequent calls.
    """
    global _fluorophore_cache
    if _fluorophore_cache:
        return _fluorophore_cache

    if yaml_path is None:
        yaml_path = os.path.join(_data_dir, "fluorophores.yaml")

    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    fluorophores = {}
    for name, props in data.get("fluorophores", {}).items():
        # Generate synthetic emission spectrum if not provided
        emission_spectrum = props.get("emission_spectrum", [])
        if not emission_spectrum:
            emission_spectrum = _generate_gaussian_spectrum(props["em_peak"])

        fluor = Fluorophore(
            name=name,
            ex_peak=props["ex_peak"],
            em_peak=props["em_peak"],
            brightness=props.get("brightness", "Moderate"),
            laser_lines=props.get("laser_lines", []),
            emission_spectrum=emission_spectrum,
            is_tandem=props.get("is_tandem", False),
            is_polymer=props.get("is_polymer", False),
            manufacturer_family=props.get("manufacturer_family", ""),
            notes=props.get("notes", ""),
        )
        fluorophores[name] = fluor

    _fluorophore_cache = fluorophores
    return fluorophores


def get_fluorophore(name: str) -> Optional[Fluorophore]:
    """Get a single fluorophore by name."""
    lib = load_fluorophores()
    return lib.get(name)


def get_fluorophores_for_laser(laser_nm: float, tolerance: float = 30.0) -> list[Fluorophore]:
    """
    Return all fluorophores that can be excited by a given laser wavelength.
    A fluorophore is compatible if the laser wavelength is within its laser_lines list.
    """
    lib = load_fluorophores()
    results = []
    for fluor in lib.values():
        if laser_nm in fluor.laser_lines:
            results.append(fluor)
        elif any(abs(laser_nm - ll) <= tolerance for ll in fluor.laser_lines):
            results.append(fluor)
    return results


def get_fluorophores_for_channel(channel: Channel) -> list[Fluorophore]:
    """
    Return all fluorophores whose emission peak falls within a channel's bandpass filter.
    """
    lib = load_fluorophores()
    results = []
    for fluor in lib.values():
        # Check if fluorophore can be excited by the channel's laser
        is_excitable = any(
            abs(channel.laser_nm - ll) <= 30 for ll in fluor.laser_lines
        )
        if not is_excitable:
            continue

        # Check if emission peak falls within filter bandpass
        if channel.filter_low <= fluor.em_peak <= channel.filter_high:
            results.append(fluor)

    return results


# ─── Spectral Overlap Calculations ──────────────────────────────────────────

def compute_spectral_overlap(fluor_a: Fluorophore, fluor_b: Fluorophore) -> float:
    """
    Compute the fractional spectral overlap between two fluorophores.
    Returns a value between 0 (no overlap) and 1 (complete overlap).
    Uses the emission spectra convolution approach.
    """
    spec_a = np.array(fluor_a.emission_spectrum)
    spec_b = np.array(fluor_b.emission_spectrum)

    if len(spec_a) == 0 or len(spec_b) == 0:
        return 0.0

    # Ensure same length
    min_len = min(len(spec_a), len(spec_b))
    spec_a = spec_a[:min_len]
    spec_b = spec_b[:min_len]

    # Normalize
    if spec_a.max() > 0:
        spec_a = spec_a / spec_a.max()
    if spec_b.max() > 0:
        spec_b = spec_b / spec_b.max()

    # Overlap integral (area of minimum)
    overlap = np.minimum(spec_a, spec_b).sum()
    total = spec_a.sum()

    if total == 0:
        return 0.0

    return float(overlap / total)


def compute_spillover(
    source_fluor: Fluorophore,
    detector_channel: Channel,
) -> float:
    """
    Compute the fraction of source_fluor's emission that spills into detector_channel.
    This is the key metric for panel design — spillover into a channel measuring
    a different fluorophore creates false signal.
    """
    if not source_fluor.emission_spectrum:
        return 0.0

    wavelengths = np.arange(400, 400 + 5 * len(source_fluor.emission_spectrum), 5)
    spectrum = np.array(source_fluor.emission_spectrum)

    if spectrum.max() == 0:
        return 0.0

    # Normalize spectrum
    spectrum = spectrum / spectrum.max()

    # Total emission
    total_emission = spectrum.sum()
    if total_emission == 0:
        return 0.0

    # Emission within detector bandpass
    mask = (wavelengths >= detector_channel.filter_low) & (
        wavelengths <= detector_channel.filter_high
    )
    in_channel = spectrum[mask].sum()

    return float(in_channel / total_emission)


def compute_spillover_matrix(
    channels: list[Channel],
    fluorophores_list: list[Fluorophore],
) -> np.ndarray:
    """
    Compute the full spillover matrix.
    Entry [i][j] = fraction of fluorophore i's signal in channel j.
    Diagonal should be highest (each fluorophore in its own channel).
    """
    n_fluor = len(fluorophores_list)
    n_chan = len(channels)
    matrix = np.zeros((n_fluor, n_chan))

    for i, fluor in enumerate(fluorophores_list):
        for j, channel in enumerate(channels):
            matrix[i][j] = compute_spillover(fluor, channel)

    return matrix


# ─── Brightness Scoring ─────────────────────────────────────────────────────

BRIGHTNESS_SCORES = {
    "Very High": 5,
    "High": 4,
    "Moderate": 3,
    "Low": 2,
    "Very Low": 1,
}


def brightness_score(fluor: Fluorophore) -> int:
    """Convert brightness string to numeric score."""
    return BRIGHTNESS_SCORES.get(fluor.brightness, 3)


def rank_fluorophores_by_brightness(fluorophores_list: list[Fluorophore]) -> list[Fluorophore]:
    """Return fluorophores sorted by brightness, brightest first."""
    return sorted(fluorophores_list, key=brightness_score, reverse=True)


def reload_fluorophores(yaml_path: Optional[str] = None):
    """Force reload of the fluorophore cache."""
    global _fluorophore_cache
    _fluorophore_cache = {}
    return load_fluorophores(yaml_path)
