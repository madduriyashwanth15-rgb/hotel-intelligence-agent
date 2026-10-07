"""Wilson score intervals used in evaluation reports (ADR-0028)."""

from __future__ import annotations

import pytest

from hotel_agent.stats import interval_block, proportion, wilson_interval


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        # Reference values for the 95% Wilson interval (textbook examples).
        (5, 10, 0.2366, 0.7634),
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (0, 30, 0.0, 0.1135),
        (30, 30, 0.8865, 1.0),
        (81, 263, 0.2553, 0.3662),
    ],
)
def test_wilson_matches_reference_values(k: int, n: int, low: float, high: float) -> None:
    lo, hi = wilson_interval(k, n)
    assert lo == pytest.approx(low, abs=5e-4)
    assert hi == pytest.approx(high, abs=5e-4)


def test_interval_contains_estimate_and_is_symmetric_in_complement() -> None:
    for n in (1, 7, 24, 36, 92):
        for k in range(n + 1):
            lo, hi = wilson_interval(k, n)
            assert 0.0 <= lo <= k / n <= hi <= 1.0
            clo, chi = wilson_interval(n - k, n)
            assert lo == pytest.approx(1 - chi)
            assert hi == pytest.approx(1 - clo)


def test_interval_narrows_with_more_cases() -> None:
    small, large = wilson_interval(18, 24), wilson_interval(180, 240)
    assert large[1] - large[0] < small[1] - small[0]


@pytest.mark.parametrize(("k", "n"), [(1, 0), (-1, 5), (6, 5)])
def test_invalid_inputs(k: int, n: int) -> None:
    with pytest.raises(ValueError, match="must"):
        wilson_interval(k, n)


def test_proportion_and_block() -> None:
    assert proportion(0, 0) is None
    assert proportion(21, 24) == {"k": 21, "n": 24, "rate": 0.875, "wilson95": [0.69, 0.9566]}
    block = interval_block({"a": (1, 2), "b": (0, 0)})
    assert block["method"].startswith("Wilson score interval, 95%")
    assert block["metrics"]["b"] is None
