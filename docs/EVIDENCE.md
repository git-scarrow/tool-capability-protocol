# EVIDENCE TLV

Canonical CBOR structure:

```
{
  "id": "ev:sha256:<hex>",
  "entries": [
    {"kind":"proof","ref":"pr:<id>"},
    {"kind":"witness","ref":"wi:<id>"}
  ]
}
```
