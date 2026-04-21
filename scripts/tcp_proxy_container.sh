#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-ensure}"
PORT="${TCP_CC_PROXY_PORT:-8742}"
IMAGE_NAME="${TCP_PROXY_IMAGE_NAME:-tcp-cc-proxy:local}"
CONTAINER_NAME="${TCP_PROXY_CONTAINER_NAME:-tcp-cc-proxy}"
PROXY_DIR="${TCP_PROXY_DIR:-$HOME/projects/tool-capability-protocol}"
STATE_ROOT="${TCP_PROXY_STATE_ROOT:-$HOME/.tcp-shadow}"
STATE_DIR="$STATE_ROOT/proxy"
PROXY_LOG="$STATE_DIR/proxy.log"
WORKSPACE_MCP_SERVERS="${TCP_PROXY_WORKSPACE_MCP_SERVERS:-bay-view-graph}"
PACK_MANIFEST="${TCP_PROXY_PACK_MANIFEST:-$PROXY_DIR/.tcp-proxy-packs.yaml}"
HOST_CWD="${TCP_PROXY_CWD:-$PROXY_DIR}"
WORKERS="${TCP_PROXY_WORKERS:-4}"

mkdir -p "$STATE_DIR"

_healthcheck() {
    curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
}

_container_running() {
    docker ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"
}

_container_exists() {
    docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"
}

_build_image_if_missing() {
    if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        return 0
    fi
    docker build -f "$PROXY_DIR/Dockerfile.tcp-cc-proxy" -t "$IMAGE_NAME" "$PROXY_DIR"
}

_remove_stopped_container() {
    if _container_exists && ! _container_running; then
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
}

start_proxy() {
    if _healthcheck; then
        exit 0
    fi

    _remove_stopped_container
    _build_image_if_missing

    if _container_running; then
        exit 0
    fi

    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -p "127.0.0.1:${PORT}:8742" \
        -e HOME=/state-home \
        -e ANTHROPIC_UPSTREAM_BASE="${ANTHROPIC_UPSTREAM_BASE:-https://api.anthropic.com}" \
        -e TCP_CC_PROXY_MODE="${TCP_CC_PROXY_MODE:-shadow}" \
        -e TCP_PROXY_WORKERS="$WORKERS" \
        ${TCP_PROXY_ALLOWED_MCP_SERVERS:+-e TCP_PROXY_ALLOWED_MCP_SERVERS="$TCP_PROXY_ALLOWED_MCP_SERVERS"} \
        -e TCP_PROXY_WORKSPACE_MCP_SERVERS="$WORKSPACE_MCP_SERVERS" \
        -e TCP_PROXY_CWD="$HOST_CWD" \
        -e TCP_PROXY_PACK_MANIFEST=/config/.tcp-proxy-packs.yaml \
        -v "$STATE_ROOT:/state-home/.tcp-shadow" \
        -v "$PACK_MANIFEST:/config/.tcp-proxy-packs.yaml:ro" \
        "$IMAGE_NAME" \
        >>"$PROXY_LOG" 2>&1

    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if _healthcheck; then
            exit 0
        fi
        sleep 1
    done

    docker logs "$CONTAINER_NAME" >>"$PROXY_LOG" 2>&1 || true
    echo "tcp proxy container failed to become healthy" >&2
    exit 1
}

rebuild_proxy() {
    stop_proxy
    docker build --no-cache -f "$PROXY_DIR/Dockerfile.tcp-cc-proxy" -t "$IMAGE_NAME" "$PROXY_DIR"
    start_proxy
}

stop_proxy() {
    if _container_exists; then
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
}

status_proxy() {
    if _container_running; then
        docker ps --filter "name=^/${CONTAINER_NAME}$"
        exit 0
    fi
    echo "tcp proxy container is not running" >&2
    exit 1
}

logs_proxy() {
    docker logs "$CONTAINER_NAME"
}

case "$ACTION" in
    ensure|start)
        start_proxy
        ;;
    stop)
        stop_proxy
        ;;
    rebuild)
        rebuild_proxy
        ;;
    status)
        status_proxy
        ;;
    logs)
        logs_proxy
        ;;
    *)
        echo "usage: $0 [ensure|start|stop|rebuild|status|logs]" >&2
        exit 2
        ;;
esac
