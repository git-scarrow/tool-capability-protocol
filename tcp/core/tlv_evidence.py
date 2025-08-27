"""Implementation of the EVIDENCE (0x000B) TLV.

This module provides a small helper for encoding and decoding
canonical CBOR payloads representing evidence.  The format is based on
an ordered map with two keys:

```
{
  "id": "ev:sha256:<hex>",
  "entries": [
      {"kind":"proof","ref":"pr:<id>","alg":"ed25519","hash":"sha256:<hex>","ts":"<RFC3339Z>"},
      {"kind":"witness","ref":"wi:<id>"}
  ]
}
```

Entries are sorted by ``(kind, ref)`` and duplicate pairs are rejected.
The encoded TLV payload must not exceed 64KiB.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

import cbor2

from .tlv_base import TLVBase

_MAX_PAYLOAD = 64 * 1024


class EvidenceTLV(TLVBase):
    """Encoder/decoder for the EVIDENCE TLV."""

    @staticmethod
    def _sorted_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set[Tuple[str, str]] = set()
        ordered = []
        for ent in sorted(entries, key=lambda e: (e.get("kind", ""), e.get("ref", ""))):
            key = (ent.get("kind"), ent.get("ref"))
            if key in seen:
                raise ValueError("duplicate evidence entry")
            seen.add(key)
            ordered.append(ent)
        return ordered

    @staticmethod
    def encode(payload: Dict[str, Any]) -> bytes:
        entries = EvidenceTLV._sorted_entries(payload.get("entries", []))
        obj = {"id": payload["id"], "entries": entries}
        data = cbor2.dumps(obj, canonical=True)
        if len(data) > _MAX_PAYLOAD:
            raise ValueError("evidence payload too large")
        return data

    @staticmethod
    def decode(data: bytes) -> Dict[str, Any]:
        obj = cbor2.loads(data)
        entries = EvidenceTLV._sorted_entries(obj.get("entries", []))
        obj["entries"] = entries
        return obj


def compute_evidence_id(meta: Dict[str, Any]) -> str:
    """Compute evidence identifier.

    The ID is ``ev:sha256:<hex>`` where the hash is taken over the
    canonical JSON encoding (sorted keys, minimal spacing) of selected
    metadata: ``{t1,t2,expected,snf1,snf2}``.
    """

    keys = {k: meta[k] for k in ["t1", "t2", "expected", "snf1", "snf2"] if k in meta}
    canon = json.dumps(keys, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canon).hexdigest()
    return f"ev:sha256:{digest}"
