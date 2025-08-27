"""Stamp test vector hex files with canonical CBOR and CRC."""
from __future__ import annotations

import argparse
import binascii
import pathlib
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tcp.core.tlv_evidence import EvidenceTLV
from tcp.core.tlv_policy import PolicyTLV


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--type", choices=["evidence", "policy"], required=True)
    args = p.parse_args()

    if args.type == "evidence":
        payload = EvidenceTLV.encode(
            {
                "id": "ev:sha256:0",
                "entries": [{"kind": "proof", "ref": "pr:demo"}],
            }
        )
    else:
        payload = PolicyTLV.encode(
            {
                "min_trust": "ADAPTER_SIGNED",
                "max_risk": 2,
                "require_predicates": ["authorized"],
            }
        )

    text = open(args.path).read()
    text = text.replace("CBOR_BYTES", binascii.hexlify(payload).decode())
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    text = text.replace("CRC32C", f"{crc:08x}")
    open(args.path, "w").write(text)
    print(f"stamped {args.path}")


if __name__ == "__main__":  # pragma: no cover
    main()
