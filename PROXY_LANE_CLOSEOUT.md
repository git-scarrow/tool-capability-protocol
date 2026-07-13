# TCP-CC Proxy Research Lane — Close-Out

**Date**: 2026-07-13
**Scope**: The live-interception lane (`tcp.proxy.cc_proxy` on port 8742,
`ANTHROPIC_BASE_URL` pointed at it globally). Ran 2026-04-07 → 2026-07-13
(~97 days), 55,437 decision rows, 5.6 GB (`~/.tcp-shadow/proxy/decisions.jsonl`).

## The question

Does TCP-style real-time tool gating deliver value (token savings + a
measurable safety signal) at acceptable degradation cost, on real Claude Code
traffic? Answer: **yes on savings, yes-but-small on safety signal, negligible
degradation** — details below. The lane is closed and the interception
decommissioned; the data and gates stand ready if it's revived.

## Answers

### 1. Token savings (answered 2026-07-04, held through close-out)

Stage 4.5 reducer in demote mode strips deferred tool schemas to name+stub:
median **66.8 KB ≈ 17,100 tokens saved per turn = 44% of the tool block**
(ratio stable across samples; per-turn p25/p50/p75 = 19.7/66.8/76.2 KB).
Gate 2 replay on live v2 data: 5,026 scoreable rows, **0.68% shortlist miss**
(PASS). Shield drift check: **100% demotion-verdict agreement** over 6,284
rows (PASS).

### 2. Degradation cost of demotion (closed 2026-07-13)

Exact same-`conversation_id` warm/cold classification over all post-flip
demote rows (2026-07-04 → 07-13: 1,378 live rows, 119 conversations, 1,545
logged tool calls):

- Calls to same-turn-deferred tools: **7 / 1,545 = 0.45%**.
- Of those 7: 2 warm (schema seen earlier in the conversation), **5 cold** —
  all to reducer-demoted tools (writing-rag ×3, bay-view-graph ×2).

The 2026-07-04 preliminary ("all warm, 0 cold") is **falsified** — cold calls
do happen. But the model made them successfully-looking without ever seeing
the schema in-conversation, almost certainly because skills/agent definitions
document those tools' parameters out-of-band. Frequency is so low (5 events /
9 days) that demotion's degradation cost is **confirmed negligible** on this
workload. Caveat: 7 events is far below the ≥2,000 gate originally set; this
is a close-out read, not a promotion-grade gate.

Note the sharp drop from the telemetry-mode preliminary (28% of calls touched
deferred tools in one 18-min sample) to 0.45% post-flip: once schemas are
actually stripped from the wire, the model largely stops calling those tools.
Whether that is "didn't need them" or "capability suppression" is exactly what
the denial gate (below) was built to watch.

### 3. Safety signal — confabulated capability denials (closed 2026-07-13)

Full-history scan (2026-07-06): 46 v1-flagged violations over 90 days, of
which 12 resolution-backed; manual review put ~5–6 as genuine confabulated
denials (model claims inability while the tool was schema-deferred) — rate
≈ 0.09% of CRG-active turns. Real but rare.

Detector v2 (`crg.absence.v2`, commit 3cd170f) dual-ran with v1 from
2026-07-06 to close-out: **3 absence-language events, 0 violations from
either detector, 0 disagreements**. Consistent with the historical base rate
(~0.4–0.6 events/day) — the planned ~07-20 disagreement review would have seen
~6 events; there is no live-data reason to wait. v2's substantive validation
remains the labeled fixture (`tests/data/absence_audit_v1.jsonl`, 46 rows:
18 genuine / 28 fp / 14 needs_review; fixture gate 5/5 genuine detected,
0/18 FP-eligible in `tests/unit/test_absence_detector_v2.py`).

Post-demote-flip check: **0 resolution-backed violations** in the 9-day demote
window (2 flag hits, both the "no resolutions attached" FP-heavy category,
both pre-v2-rebuild). Expected count at the historical rate was ~0.3, so this
is consistent, not proof of improvement.

**v1→v2 flip decision: moot.** With the lane decommissioned there is no live
detector to promote. If revived, flip on the fixture gate + a fresh dual-run
window.

## Parked (would be the next work if revived)

- **Argument capture** in `tool_call_sequence` — 47.4% of egress is Bash and
  logged as tool-name only; args are the single schema change that unlocks
  command-level TCP gating (the original 24-byte-descriptor vision).
- **Response-side capability resolution** — Rule-2 genuine denials get no
  resolutions attached when the prompt lacks capability keywords.
- **Real risk descriptors on the live path** — `tcp/proxy/projection.py`
  hardcodes `risk_level="safe"`; TCP risk flags never reached the proxy.
- **Request-derivation precision** (TCP-VAL-1 bottleneck, 5.4%) — unchanged.
- Absence-audit fixture: 14 `needs_review` rows still unlabeled.

## Known limitation recorded

Remote Control cannot work through the proxy; remediation ruled out after
live testing — see `KNOWN_LIMITATION_REMOTE_CONTROL.md`.

## Decommission

- `ANTHROPIC_BASE_URL` export commented out in `~/.bashrc` / `~/.zshrc`
  (2026-07-13); new sessions go direct to `api.anthropic.com` (restores
  Remote Control eligibility). Verified with a scrubbed-env login shell.
- The only keepalive was the container's Docker `unless-stopped` restart
  policy — no cron/systemd/hook wiring existed (the SessionStart-hook comment
  in `scripts/tcp_proxy_ensure.sh` was stale).
- Final step (run after the last proxied session ends, since stopping the
  container kills any session still routed through it):
  `docker stop tcp-cc-proxy && zstd -T0 --rm ~/.tcp-shadow/proxy/decisions.jsonl`
  (`unless-stopped` keeps it stopped across reboots; the 5.6 GB decision log
  compresses in place).
- **To revive**: re-export `ANTHROPIC_BASE_URL=http://127.0.0.1:8742` in the
  shell rc files, `./scripts/tcp_proxy_container.sh start`, decompress the
  decision log if the startup registry-warm should seed from it. Reducer flag
  default is already `demote` in `scripts/tcp_proxy_container.sh`; proxy mode
  is read from the `~/.tcp-shadow/proxy/mode` FILE (overrides env).
