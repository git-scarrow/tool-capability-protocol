"""TCP-CC Proxy: HTTP shim for Claude Code → Anthropic with optional tool gating."""

from __future__ import annotations

from tcp.proxy.projection import ProjectionTier, project_anthropic_tools
from tcp.proxy.prompt_select import extract_task_prompt

__all__: list[str] = [
    "ProjectionTier",
    "extract_task_prompt",
    "project_anthropic_tools",
]
