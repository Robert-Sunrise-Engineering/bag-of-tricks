"""Unsupervised per-layer match-threshold calibration.

No ``arcgis`` import here -- this module consumes only the plain simplified
feature dicts that ``conflate.features.simplify_feature`` produces, following
the codebase's existing pure/AGOL-integration split.

There is no ground-truth labeled data for a given layer pair, so the
threshold is estimated from the *shape* of the nearest-neighbor distance
distribution instead: in a real conflation dataset that distribution is
typically bimodal -- a tight low-distance cluster of true correspondences
(offset only by GPS/survey error) and a diffuse high-distance tail of
captured features with no real authoritative counterpart. The natural
threshold is the valley between those two modes. When the distribution
doesn't show a clear valley (too little data, or no real separation), a
robust fallback statistic is used instead and the result is marked
low-confidence.
"""

import logging
import math

import numpy as np
from scipy.signal import find_peaks
from scipy.stats import gaussian_kde

from conflate.geometry import geodesic_distance

logger = logging.getLogger(__name__)

_LOG_OFFSET = 0.01  # avoids log(0) for exact-coincidence distances
_GRID_SIZE = 512


def nearest_distances(
    captured_features: list[dict],
    authoritative_features: list[dict],
    type_field_authoritative: str | None,
    type_field_captured: str | None,
) -> list[float]:
    """For each captured feature, the geodesic distance (in meters) to its
    nearest type-matching authoritative feature, with no distance cap.

    Unlike production matching (conflate.matching.find_candidates), this
    performs an unbounded search -- the whole point of calibration is to see
    where the far tail of unmatched captured features actually sits, which a
    threshold-limited search would hide by construction.

    Args:
        captured_features: Simplified feature dicts (see
            conflate.features.simplify_feature), each with "lon"/"lat".
        authoritative_features: Same shape, for the authoritative side.
        type_field_authoritative: Field name in authoritative features for
            type, or None to skip type matching.
        type_field_captured: Field name in captured features for type, or
            None to skip type matching.

    Returns:
        One distance per captured feature that has at least one
        type-matching authoritative candidate, in captured_features' order.
        Captured features with zero type-matching candidates are skipped
        (logged, not errored) -- there's nothing to measure a distance to.
    """
    check_type = type_field_authoritative is not None and type_field_captured is not None

    by_type: dict = {}
    if check_type:
        for auth in authoritative_features:
            by_type.setdefault(auth.get(type_field_authoritative), []).append(auth)

    distances = []
    n_skipped = 0
    for captured in captured_features:
        if check_type:
            candidates = by_type.get(captured.get(type_field_captured), [])
        else:
            candidates = authoritative_features

        if not candidates:
            n_skipped += 1
            continue

        nearest = min(
            geodesic_distance(captured["lon"], captured["lat"], auth["lon"], auth["lat"])
            for auth in candidates
        )
        distances.append(nearest)

    if n_skipped:
        logger.info(
            "Calibration: skipped %d captured feature(s) with no type-matching "
            "authoritative candidate.",
            n_skipped,
        )

    return distances


def _mad_fallback(distances: list[float], n_samples: int) -> dict:
    arr = np.asarray(distances, dtype=float)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    suggested = median + 3 * mad
    fraction_within = float(np.mean(arr <= suggested))
    return {
        "suggested_threshold_m": suggested,
        "confidence": "low_no_clear_bimodal_separation",
        "n_samples": n_samples,
        "fraction_within_suggested": fraction_within,
        "distance_summary": _distance_summary(arr),
    }


def _distance_summary(arr: np.ndarray) -> dict:
    percentiles = np.percentile(arr, [0, 25, 50, 75, 90, 95, 100])
    return {
        "min": float(percentiles[0]),
        "p25": float(percentiles[1]),
        "median": float(percentiles[2]),
        "p75": float(percentiles[3]),
        "p90": float(percentiles[4]),
        "p95": float(percentiles[5]),
        "max": float(percentiles[6]),
    }


def suggest_threshold(distances: list[float], min_samples: int = 8) -> dict:
    """Suggest a match_threshold_m from a layer's nearest-neighbor distance
    distribution (see module docstring for the bimodal-valley rationale).

    Args:
        distances: Output of nearest_distances -- one nearest-candidate
            distance per captured feature, in meters.
        min_samples: Minimum number of distances required to attempt KDE
            valley detection at all; below this, there's too little data to
            trust a density estimate and the MAD fallback is used directly.

    Returns:
        {
            "suggested_threshold_m": float | None,  # None only for insufficient_data
            "confidence": "clear_bimodal_valley" | "low_no_clear_bimodal_separation"
                | "insufficient_data",
            "n_samples": int,
            "fraction_within_suggested": float | None,
            "distance_summary": {"min", "p25", "median", "p75", "p90", "p95", "max"} | None,
        }
    """
    n_samples = len(distances)
    if n_samples < min_samples:
        return {
            "suggested_threshold_m": None,
            "confidence": "insufficient_data",
            "n_samples": n_samples,
            "fraction_within_suggested": None,
            "distance_summary": _distance_summary(np.asarray(distances, dtype=float))
            if n_samples
            else None,
        }

    log_distances = np.log(np.asarray(distances, dtype=float) + _LOG_OFFSET)

    try:
        kde = gaussian_kde(log_distances)
        grid = np.linspace(log_distances.min(), log_distances.max(), _GRID_SIZE)
        density = kde(grid)

        peak_idx, _ = find_peaks(density)
        valley_idx, _ = find_peaks(-density)

        if len(peak_idx) >= 2 and len(valley_idx) >= 1:
            first_peak = peak_idx[0]
            valleys_after_first_peak = valley_idx[valley_idx > first_peak]
            if len(valleys_after_first_peak) > 0:
                valley = valleys_after_first_peak[0]
                suggested_log = grid[valley]
                suggested = float(math.exp(suggested_log) - _LOG_OFFSET)
                arr = np.asarray(distances, dtype=float)
                fraction_within = float(np.mean(arr <= suggested))
                return {
                    "suggested_threshold_m": suggested,
                    "confidence": "clear_bimodal_valley",
                    "n_samples": n_samples,
                    "fraction_within_suggested": fraction_within,
                    "distance_summary": _distance_summary(arr),
                }
    except np.linalg.LinAlgError:
        # gaussian_kde raises when the input is degenerate (e.g. near-zero
        # variance) -- fall through to the MAD fallback below.
        pass

    return _mad_fallback(distances, n_samples)
