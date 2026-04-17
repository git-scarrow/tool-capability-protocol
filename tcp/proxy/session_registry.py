"""Session attribution and lifecycle artifacts for the shared TCP proxy.

Phase 1 keeps the stable shared listener on 127.0.0.1:8742, but routes and
telemeters requests with a proxy-local session identity derived from the owning
Claude process behind each loopback TCP connection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


_PROC_TCP_PATH = Path("/proc/net/tcp")
_PROC_ROOT = Path("/proc")


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    session_start_ts: float
    client_pid: int | None
    client_cwd: str
    proxy_pid: int
    proxy_port: int
    concurrent_sessions: int
    lock_path: Path
    log_path: Path
    decisions_path: Path


class SessionRegistry:
    """Resolve and persist session-local artifacts for the shared proxy.

    Attribution is best-effort from the loopback peer connection. Once a peer
    port has been resolved to a Claude PID we cache it, persist a lockfile, and
    reuse the generated session identity for subsequent requests on that socket.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        proxy_port: int,
        proxy_pid: int | None = None,
        proc_root: Path = _PROC_ROOT,
        proc_tcp_path: Path = _PROC_TCP_PATH,
    ) -> None:
        self._state_dir = state_dir
        self._sessions_dir = state_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._proxy_port = proxy_port
        self._proxy_pid = proxy_pid if proxy_pid is not None else os.getpid()
        self._proc_root = proc_root
        self._proc_tcp_path = proc_tcp_path
        self._by_peer: dict[tuple[str, int], SessionContext] = {}
        self._last_reap_at = 0.0

    def context_for_peer(self, client_host: str, client_port: int) -> SessionContext:
        self._reap_stale_sessions()
        peer = (client_host, client_port)
        existing = self._by_peer.get(peer)
        if existing and self._pid_is_alive(existing.client_pid):
            return self._refresh(existing)

        client_pid = self._resolve_client_pid(client_host, client_port)
        client_cwd = self._resolve_cwd(client_pid)
        session_id = self._session_id_for(client_pid, client_cwd, peer)
        session_start_ts = time.time()
        session_dir = self._sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        ctx = SessionContext(
            session_id=session_id,
            session_start_ts=session_start_ts,
            client_pid=client_pid,
            client_cwd=client_cwd,
            proxy_pid=self._proxy_pid,
            proxy_port=self._proxy_port,
            concurrent_sessions=0,
            lock_path=session_dir / "session.lock",
            log_path=session_dir / "requests.jsonl",
            decisions_path=session_dir / "decisions.jsonl",
        )
        self._persist_lock(ctx)
        ctx = self._refresh(ctx)
        self._by_peer[peer] = ctx
        return ctx

    def append_request_event(self, ctx: SessionContext, record: dict[str, object]) -> None:
        self._append_jsonl(ctx.log_path, record)

    def _refresh(self, ctx: SessionContext) -> SessionContext:
        concurrent_sessions = self._count_live_sessions()
        refreshed = SessionContext(
            session_id=ctx.session_id,
            session_start_ts=ctx.session_start_ts,
            client_pid=ctx.client_pid,
            client_cwd=ctx.client_cwd,
            proxy_pid=ctx.proxy_pid,
            proxy_port=ctx.proxy_port,
            concurrent_sessions=concurrent_sessions,
            lock_path=ctx.lock_path,
            log_path=ctx.log_path,
            decisions_path=ctx.decisions_path,
        )
        self._persist_lock(refreshed)
        return refreshed

    def _persist_lock(self, ctx: SessionContext) -> None:
        payload = {
            "session_id": ctx.session_id,
            "session_start_ts": ctx.session_start_ts,
            "client_pid": ctx.client_pid,
            "client_cwd": ctx.client_cwd,
            "proxy_pid": ctx.proxy_pid,
            "proxy_port": ctx.proxy_port,
            "last_seen_ts": time.time(),
        }
        tmp = ctx.lock_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, ctx.lock_path)

    def _count_live_sessions(self) -> int:
        count = 0
        for lock_path in self._sessions_dir.glob("*/session.lock"):
            payload = self._read_lock(lock_path)
            if payload and self._pid_is_alive(payload.get("client_pid")):
                count += 1
        return count

    def _reap_stale_sessions(self) -> None:
        now = time.time()
        if now - self._last_reap_at < 5.0:
            return
        self._last_reap_at = now

        active_peers: dict[tuple[str, int], SessionContext] = {}
        for peer, ctx in list(self._by_peer.items()):
            if self._pid_is_alive(ctx.client_pid):
                active_peers[peer] = ctx
        self._by_peer = active_peers

        for lock_path in self._sessions_dir.glob("*/session.lock"):
            payload = self._read_lock(lock_path)
            if payload and not self._pid_is_alive(payload.get("client_pid")):
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass

    def _read_lock(self, path: Path) -> dict[str, object] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _pid_is_alive(self, pid: object) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        return (self._proc_root / str(pid)).exists()

    def _resolve_cwd(self, pid: int | None) -> str:
        if pid is None:
            return ""
        try:
            return os.readlink(self._proc_root / str(pid) / "cwd")
        except OSError:
            return ""

    def _session_id_for(
        self,
        client_pid: int | None,
        client_cwd: str,
        peer: tuple[str, int],
    ) -> str:
        material = f"{client_pid}:{client_cwd}:{peer[0]}:{peer[1]}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        if client_pid is not None:
            return f"proxy-{client_pid}-{digest}"
        return f"proxy-peer-{peer[1]}-{digest}"

    def _resolve_client_pid(self, client_host: str, client_port: int) -> int | None:
        if client_host not in {"127.0.0.1", "::1", "localhost"}:
            return None
        ss_pid = self._lookup_pid_via_ss(client_port)
        if ss_pid is not None:
            return ss_pid
        inode = self._lookup_inode_for_peer_port(client_port)
        if inode is None:
            return None
        return self._lookup_pid_by_inode(inode)

    def _lookup_pid_via_ss(self, client_port: int) -> int | None:
        try:
            result = subprocess.run(
                ["ss", "-tnp"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None

        target_local = f":{client_port}"
        target_remote = f":{self._proxy_port}"
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("ESTAB"):
                continue
            if target_local not in line or target_remote not in line:
                continue
            columns = line.split()
            if len(columns) < 6:
                continue
            local_addr = columns[3]
            remote_addr = columns[4]
            if not local_addr.endswith(target_local):
                continue
            if not remote_addr.endswith(target_remote):
                continue
            match = re.search(r"pid=(\d+)", line)
            if match is None:
                continue
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _lookup_inode_for_peer_port(self, client_port: int) -> int | None:
        try:
            lines = self._proc_tcp_path.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            return None
        target_local = f"{self._proxy_port:04X}"
        target_remote = f"{client_port:04X}"
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_addr, remote_addr, state = parts[1], parts[2], parts[3]
            inode = parts[9]
            try:
                local_port = local_addr.split(":")[1]
                remote_port = remote_addr.split(":")[1]
            except IndexError:
                continue
            if local_port == target_local and remote_port == target_remote and state == "01":
                try:
                    return int(inode)
                except ValueError:
                    return None
        return None

    def _lookup_pid_by_inode(self, inode: int) -> int | None:
        needle = f"socket:[{inode}]"
        for proc_path in self._proc_root.iterdir():
            if not proc_path.name.isdigit():
                continue
            fd_dir = proc_path / "fd"
            if not fd_dir.exists():
                continue
            try:
                for fd_path in fd_dir.iterdir():
                    try:
                        if os.readlink(fd_path) == needle:
                            return int(proc_path.name)
                    except OSError:
                        continue
            except OSError:
                continue
        return None

    def _append_jsonl(self, path: Path, record: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
