"""Selector Normal Form utilities."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable


class SNFError(ValueError):
    """Raised when a selector cannot be normalized."""


_VAR_RE = re.compile(r"^\{[^{}]+\}$")
_SCHEMES = {"file://", "git://", "http://", "https://"}


@dataclass
class SNFCanonicalizer:
    """Convert selector strings to Selector Normal Form (SNF)."""

    def _split(self, s: str) -> tuple[str, str]:
        scheme = ""
        for sch in _SCHEMES:
            if s.startswith(sch):
                scheme = sch
                s = s[len(sch) :]
                break
        if not s.startswith("/"):
            s = "/" + s
        return scheme, s

    def _normalize_path(self, path: str) -> str:
        segments = []
        var_index = 1
        for raw in filter(None, path.split("/")):
            if raw == ".":
                continue
            if raw == "..":
                raise SNFError("parent traversal not allowed")
            seg = raw
            if _VAR_RE.match(raw) or raw.isdigit():
                seg = f"{{x{var_index}}}"
                var_index += 1
            segments.append(seg)
        if segments and segments[-1] == "*":
            segments[-1] = "**"
        return "/" + "/".join(segments)

    def to_snf(self, selector: str) -> str:
        scheme, path = self._split(selector)
        snf_path = self._normalize_path(path)
        # Collapse '**/**' like patterns: multiple '*' segments -> '**'
        # but preserve single '*' when alone.
        collapsed: list[str] = []
        for seg in snf_path.split("/"):
            if seg == "*" and collapsed and collapsed[-1] == "*":
                collapsed[-1] = "**"
            else:
                collapsed.append(seg)
        snf_path = "/".join(collapsed)
        return f"{scheme}{snf_path}"

    def key(self, selector: str) -> bytes:
        snf = self.to_snf(selector).encode()
        return hashlib.sha256(snf).digest()
