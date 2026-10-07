"""Binomial confidence intervals for evaluation reports (Phase 7, ADR-0028).

Every evaluation metric here is a proportion of independent cases (k successes out of n).
We report the Wilson score interval at 95%: unlike the normal ("Wald") approximation it
stays inside [0, 1] and remains informative at small n and at 0% / 100%, which is exactly
where these sets live (e.g. 30 adversarial cases, 34 unsafe SQL cases).

An interval describes sampling uncertainty over cases drawn like these; it says nothing
about cases unlike the set, and no significance test is implied.
"""

from __future__ import annotations

import math
from typing import Any

Z_95 = 1.959963984540054  # two-sided 95% normal quantile
METHOD = "Wilson score interval, 95%, per metric (k successes / n cases); not a significance test"


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within 0..n")
    p = successes / n
    z2 = z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    # The bounds are exactly 0 at k = 0 and exactly 1 at k = n; avoid float residue there.
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == n else min(1.0, centre + half)
    return low, high


def proportion(successes: int, n: int) -> dict[str, Any] | None:
    """{"k", "n", "rate", "wilson95": [low, high]}, or None when there are no cases."""
    if n <= 0:
        return None
    low, high = wilson_interval(successes, n)
    return {
        "k": successes,
        "n": n,
        "rate": round(successes / n, 4),
        "wilson95": [round(low, 4), round(high, 4)],
    }


def interval_block(metrics: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """Confidence-interval section for a report: metric -> (successes, n)."""
    return {
        "method": METHOD,
        "metrics": {name: proportion(k, n) for name, (k, n) in metrics.items()},
    }
