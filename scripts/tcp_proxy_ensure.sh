#!/usr/bin/env bash
# TCP-CC Proxy keepalive — called from SessionStart hook.
# Starts the proxy if it isn't already listening on port 8742.

PORT=8742
PROXY_LOG="$HOME/.tcp-shadow/proxy/proxy.log"
PROXY_DIR="$HOME/projects/tool-capability-protocol"

mkdir -p "$HOME/.tcp-shadow/proxy"

# Check if already listening
if ss -tlnp 2>/dev/null | grep -q ":${PORT}"; then
    exit 0
fi

# Start proxy in background, detached from this shell
nohup "$PROXY_DIR/.venv/bin/python" -m tcp.proxy.cc_proxy \
    --host 127.0.0.1 --port "$PORT" \
    >> "$PROXY_LOG" 2>&1 &

# Give it a moment to bind
sleep 1
