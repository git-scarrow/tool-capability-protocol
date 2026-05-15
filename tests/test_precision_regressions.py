import pytest

from tcp.core.descriptors import CapabilityFlags
from tcp.derivation.request_derivation import _derive_capability_flags_from_prompt_only


def test_network_false_positives():
    # "post" in "post-processing" should not trigger SUPPORTS_NETWORK
    prompt = (
        "analysis on SLM usage for real-time processing: ... web search post-processing"
    )
    flags = _derive_capability_flags_from_prompt_only(prompt)
    assert not (
        flags & CapabilityFlags.SUPPORTS_NETWORK
    ), f"SUPPORTS_NETWORK triggered by 'post-processing' in: {prompt}"

    # "request" should not trigger SUPPORTS_NETWORK on its own
    prompt = "I'll make a request to the filesystem"
    flags = _derive_capability_flags_from_prompt_only(prompt)
    assert not (
        flags & CapabilityFlags.SUPPORTS_NETWORK
    ), f"SUPPORTS_NETWORK triggered by 'request' in: {prompt}"


def test_file_false_positives():
    # "e.g." should not trigger SUPPORTS_FILES
    prompt = "any patterns appear (e.g., fast model does quick checks)"
    flags = _derive_capability_flags_from_prompt_only(prompt)
    assert not (
        flags & CapabilityFlags.SUPPORTS_FILES
    ), f"SUPPORTS_FILES triggered by 'e.g.' in: {prompt}"

    # "npm" should not trigger SUPPORTS_FILES
    prompt = "March 31 2026 npm sourcemap version"
    flags = _derive_capability_flags_from_prompt_only(prompt)
    assert not (
        flags & CapabilityFlags.SUPPORTS_FILES
    ), f"SUPPORTS_FILES triggered by 'npm' in: {prompt}"
