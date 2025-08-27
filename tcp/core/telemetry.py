"""Simple telemetry helpers."""
from __future__ import annotations

from statistics import median
from typing import Dict, List


def _percentile(sorted_vals: List[int], pct: float) -> int:
    if not sorted_vals:
        return 0
    k = int(round((len(sorted_vals) - 1) * pct))
    return sorted_vals[k]


def record_hist(name: str, ns_values: List[int]) -> Dict[str, int]:
    """Return histogram percentiles for the provided latency values."""
    vals = sorted(ns_values)
    return {
        "p50": _percentile(vals, 0.50),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
    }
