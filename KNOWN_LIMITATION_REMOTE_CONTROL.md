# Known Limitation: TCP-CC Proxy Blocks Claude Code Remote Control

**Filed**: 2026-07-13
**Component**: `tcp.proxy.cc_proxy` (`Dockerfile.tcp-cc-proxy`, container `tcp-cc-proxy`, port 8742)

## Problem

On any host where `ANTHROPIC_BASE_URL` is pointed at the TCP-CC proxy
(`http://127.0.0.1:8742`, set globally in `~/.bashrc` / `~/.zshrc` on gentoo),
Claude Code's **Remote Control** feature (mobile push / claude.ai session
bridge) is permanently unavailable.

`claude doctor` reports the exact gate:

```
Remote Control
Remote Control is only available when using Claude via api.anthropic.com.
- Not connected to the Anthropic API (api.anthropic.com)
```

## Root cause

Remote Control appears to require the CLI to detect a direct connection to
`api.anthropic.com`. Since the proxy sits in the request path (by design —
it's how request derivation gates tool visibility per `tcp/derivation/
request_derivation.py`), the client can never satisfy that check, regardless
of what the proxy actually forwards.

This is a side effect of the interception model, not a bug in a specific
request handler — it's unrelated to the existing 405 / MCP-allowlist issues
already logged in `CLAUDE.md`.

## Evidence

- `ss -tlnp` / `lsof -i :8742`: proxy owns the socket, active `claude` client
  connections confirmed routed through it.
- `env | grep ANTHROPIC_BASE_URL` → `http://127.0.0.1:8742` on gentoo, set in
  both shell rc files (not session-local).
- `~/.claude.json` shows the account (`claude_max`, 20x tier) and CLI version
  (2.1.207, well above `tengu_bridge_min_version` 2.1.139) are otherwise
  eligible — this is purely the proxy-detection gate.

## Remediation #1 — investigated and ruled out (2026-07-13)

Traced the actual gate in the installed CLI binary
(`~/.local/share/claude/versions/2.1.207`, minified JS, readable via
`strings`). The "connected to api.anthropic.com" check is:

```js
function gLn(){
  let e = process.env.ANTHROPIC_BASE_URL;
  if(!e) return true;
  return new URL(e).host === "api.anthropic.com";   // literal string match
}
function $$t(){ // isBridgeFirstParty — feeds the doctor line
  if(!Ru()) return false;                            // provider must be "firstParty"
  return !!process.env.ANTHROPIC_UNIX_SOCKET || gLn();
}
```

So the check is env-var/string based, not TLS-pinned — confirming the
premise. There's even a first-class escape hatch already in the CLI:
`ANTHROPIC_UNIX_SOCKET`, which short-circuits `$$t()` to `true` regardless
of `ANTHROPIC_BASE_URL`, and which the CLI's own debug text describes as
built for exactly this topology ("`ANTHROPIC_UNIX_SOCKET` is set (claude ssh
remote), and the local proxy is API-key-authed").

**Live-tested it.** Started a throwaway `uvicorn` instance serving
`cc_proxy.build_app()` over a Unix domain socket (`/tmp/cc-proxy-test.sock`,
alongside — not replacing — the production TCP listener on 8742), then ran:

```
env -u ANTHROPIC_BASE_URL ANTHROPIC_UNIX_SOCKET=/tmp/cc-proxy-test.sock claude doctor
```

| Setup | `claude doctor` → Remote Control |
|---|---|
| Baseline (`ANTHROPIC_BASE_URL=http://127.0.0.1:8742`) | `Not connected to the Anthropic API (api.anthropic.com)` |
| `ANTHROPIC_UNIX_SOCKET` → same proxy app, over a UDS | `claude.ai subscription auth not active` |

The base-URL gate did clear. But `ANTHROPIC_UNIX_SOCKET` also flips the
CLI's OAuth-detection function (`yv()`), which only recognizes the session
as OAuth-authenticated when `ANTHROPIC_UNIX_SOCKET` is set *and*
`CLAUDE_CODE_OAUTH_TOKEN` (a long-lived `claude setup-token` credential) is
also present. The normal browser-login session (what this account
actually uses) stops counting, so it trades one Remote Control gate
failure for another. And per the CLI's own text, long-lived
`CLAUDE_CODE_OAUTH_TOKEN` credentials are scope-limited ("inference-only")
and would likely fail the separate `user:profile` scope check Remote
Control also requires — so supplying one wouldn't close the loop either,
just move the failure a third time.

**Conclusion**: not a simple env-var workaround. The CLI's auth-precedence
logic treats "local proxy in the request path" and "browser-OAuth session"
as mutually exclusive by design (this is the same code path used for SSH
remote / host-managed-auth setups, which don't carry a normal claude.ai
session). Making Remote Control work through `cc_proxy` would mean either
convincing the CLI it's in one of those alternate auth modes (with its own
tradeoffs) or forwarding a full second, unproxied credential — not worth
pursuing against TCP-VAL-1 priority.

## Possible remediations (not prioritized against TCP-VAL-1)

1. ~~If Remote Control's "direct connection" check is a reachable~~
   ~~hostname/header check rather than TLS-pinned, have `cc_proxy` special-case~~
   ~~and pass through whatever channel that check uses...~~ — **Ruled out
   2026-07-13**, see above.
2. Document this as an accepted tradeoff and ship a documented escape
   hatch — e.g. a `claude-direct` wrapper that runs
   `env -u ANTHROPIC_BASE_URL claude` for sessions that need Remote Control —
   so it doesn't get re-diagnosed from scratch each time. **Recommended
   path forward**, not yet built.

No action taken beyond the #1 investigation; recording so none of this
needs to be re-discovered.
