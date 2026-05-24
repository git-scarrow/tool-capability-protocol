"""Throwaway file to smoke-test the self-hosted LLM review pipeline.

Safe to delete once the CI chain (runner → quality gates → LLM review comment)
is verified end to end.
"""


def add(a: int, b: int) -> int:
    return a + b
