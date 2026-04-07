"""Three-lane benchmark reporting for the layered deterministic router.

Splits trial metrics into deterministic / ambiguous / no-match lanes
and computes per-lane statistics.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcp.agent.loop import LoopMetrics


@dataclass(frozen=True)
class LaneReport:
    """Per-lane summary statistics."""

    deterministic_count: int
    deterministic_correct_rate: float
    deterministic_mean_latency_ms: float

    ambiguous_count: int
    ambiguous_correct_rate: float
    ambiguous_correct_any_rate: float
    ambiguous_mean_latency_ms: float
    ambiguous_mean_tokens: float

    no_match_count: int
    no_match_correct_rate: float

    bypass_ratio: float
    ambiguous_llm_lift: float

    def summary_table(self) -> str:
        lines = [
            f"{'Lane':<20} {'Count':>6} {'1st-tool':>9} {'any-pos*':>9} {'Latency':>10} {'Tokens':>8}",
            "-" * 65,
            f"{'Deterministic':<20} {self.deterministic_count:>6} "
            f"{self.deterministic_correct_rate:>8.0%} "
            f"{'—':>9} "
            f"{self.deterministic_mean_latency_ms:>9.0f}ms {'—':>8}",
            f"{'Ambiguous':<20} {self.ambiguous_count:>6} "
            f"{self.ambiguous_correct_rate:>8.0%} "
            f"{self.ambiguous_correct_any_rate:>8.0%} "
            f"{self.ambiguous_mean_latency_ms:>9.0f}ms "
            f"{self.ambiguous_mean_tokens:>8.0f}",
            f"{'No-match':<20} {self.no_match_count:>6} "
            f"{self.no_match_correct_rate:>8.0%} "
            f"{'—':>9} {'—':>10} {'—':>8}",
            "-" * 65,
            f"Bypass ratio: {self.bypass_ratio:.0%}",
            f"Ambiguous LLM lift: {self.ambiguous_llm_lift:+.0%}",
            "* any-pos is primary for ambiguous lane (multi-tool calls are valid)",
        ]
        return "\n".join(lines)


def build_lane_report(
    metrics: list[LoopMetrics],
    *,
    select_best_correct_rate: float = 0.0,
) -> LaneReport:
    """Build a three-lane report from a flat list of LoopMetrics."""
    det = [m for m in metrics if m.route_confidence == "deterministic"]
    amb = [m for m in metrics if m.route_confidence == "ambiguous"]
    nm = [m for m in metrics if m.route_confidence == "no_match"]

    det_n = len(det)
    amb_n = len(amb)
    nm_n = len(nm)
    total = len(metrics)

    det_correct = sum(1 for m in det if m.selected_tool_correct) / det_n if det_n else 0.0
    amb_correct = sum(1 for m in amb if m.selected_tool_correct) / amb_n if amb_n else 0.0
    amb_any = sum(1 for m in amb if m.expected_tool_any_position) / amb_n if amb_n else 0.0
    nm_correct = sum(1 for m in nm if m.selected_tool_correct) / nm_n if nm_n else 0.0

    det_latency = sum(m.total_response_time_ms for m in det) / det_n if det_n else 0.0
    amb_latency = sum(m.total_response_time_ms for m in amb) / amb_n if amb_n else 0.0
    amb_tokens = sum(m.input_tokens for m in amb) / amb_n if amb_n else 0.0

    bypass = sum(1 for m in metrics if m.llm_bypassed) / total if total else 0.0
    lift = amb_correct - select_best_correct_rate

    return LaneReport(
        deterministic_count=det_n,
        deterministic_correct_rate=det_correct,
        deterministic_mean_latency_ms=det_latency,
        ambiguous_count=amb_n,
        ambiguous_correct_rate=amb_correct,
        ambiguous_correct_any_rate=amb_any,
        ambiguous_mean_latency_ms=amb_latency,
        ambiguous_mean_tokens=amb_tokens,
        no_match_count=nm_n,
        no_match_correct_rate=nm_correct,
        bypass_ratio=bypass,
        ambiguous_llm_lift=lift,
    )
