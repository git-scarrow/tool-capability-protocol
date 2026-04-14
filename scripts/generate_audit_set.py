#!/usr/bin/env python3
"""Generate a synthetic smoke-test audit set.

This fixture is for development smoke tests only. It is intentionally separate
from the TCP-DATA-1 validation path, which requires a real hand-labeled audit
set drawn from shadow-session telemetry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tcp.core.descriptors import CapabilityFlags


SMOKE_CASES = [
    {
        "session_id": "synthetic_files_1",
        "prompt": "Please open the README.md file in the repository and show the relevant lines as plain text.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_files_2",
        "prompt": "Read the local config.yaml file, summarize the active settings, and return the answer as plain text.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_network_1",
        "prompt": "Fetch https://example.com/api/status and return the response body as plain text for inspection.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.SUPPORTS_NETWORK),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_network_2",
        "prompt": "Download the JSON payload from https://example.com/api/report and return it as structured json.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.SUPPORTS_NETWORK),
        "ground_truth_formats": ["text", "json"],
    },
    {
        "session_id": "synthetic_auth_1",
        "prompt": "Use sudo to restart the nginx service and report the outcome in plain text.",
        "ground_truth_flags": int(CapabilityFlags.AUTH_REQUIRED),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_files_auth_1",
        "prompt": "Use sudo to open the local secrets.yaml file, inspect the active settings, and answer in plain text.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.AUTH_REQUIRED),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_files_network_1",
        "prompt": "Compare the local config.yaml file against the remote response at https://example.com/api/config and summarize the differences in text.",
        "ground_truth_flags": int(CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.SUPPORTS_NETWORK),
        "ground_truth_formats": ["text"],
    },
    {
        "session_id": "synthetic_files_network_auth_1",
        "prompt": "Download the deployment manifest from https://example.com/api/manifest, write the update into the local config.json file with sudo, and return a json summary.",
        "ground_truth_flags": int(
            CapabilityFlags.SUPPORTS_FILES
            | CapabilityFlags.SUPPORTS_NETWORK
            | CapabilityFlags.AUTH_REQUIRED
        ),
        "ground_truth_formats": ["text", "json"],
    },
]


def generate() -> None:
    out_path = REPO_ROOT / "tests" / "data" / "synthetic_audit_set.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(SMOKE_CASES, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(SMOKE_CASES)} smoke-test samples to {out_path}")


if __name__ == "__main__":
    generate()
