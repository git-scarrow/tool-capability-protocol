"""TCP agent experiment apparatus for EXP-2.

Instrumented agent loop, mock executors, and paired benchmark runner
for measuring TCP pre-filtering effects on Claude's tool selection
and latency.
"""

from tcp.agent.benchmark import (
    BenchmarkReport,
    PairedTrial,
    SmokeResult,
    run_paired_benchmark,
    run_smoke_test,
)
from tcp.agent.loop import ErrorKind, LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.preflight import PreflightReport, run_preflight
from tcp.agent.tasks import AgentTask, build_agent_tasks

__all__ = [
    "AgentTask",
    "BenchmarkReport",
    "ErrorKind",
    "LoopMetrics",
    "PairedTrial",
    "PreflightReport",
    "SmokeResult",
    "build_agent_tasks",
    "get_mock_executor",
    "run_agent_loop",
    "run_paired_benchmark",
    "run_preflight",
    "run_smoke_test",
]
