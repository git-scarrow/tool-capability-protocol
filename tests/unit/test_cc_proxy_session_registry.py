from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

from tcp.proxy.session_registry import SessionRegistry


def _write_proc_tcp(path: Path, *, proxy_port: int, client_port: int, inode: int) -> None:
    path.write_text(
        "\n".join(
            [
                "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode",
                (
                    "   0: 0100007F:"
                    f"{proxy_port:04X} 0100007F:{client_port:04X} 01 00000000:00000000 "
                    "00:00000000 00000000  1000        0 "
                    f"{inode} 1 0000000000000000 20 4 30 10 -1"
                ),
            ]
        ),
        encoding="utf-8",
    )


def test_context_for_peer_persists_lock_and_paths(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "4242").mkdir()
    (proc_root / "4242" / "fd").mkdir()
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    proc_tcp_path = tmp_path / "proc_tcp"
    _write_proc_tcp(proc_tcp_path, proxy_port=8742, client_port=54000, inode=12345)

    registry = SessionRegistry(
        state_dir=tmp_path / "state",
        proxy_port=8742,
        proxy_pid=777,
        proc_root=proc_root,
        proc_tcp_path=proc_tcp_path,
    )
    registry._lookup_pid_by_inode = lambda inode: 4242  # type: ignore[method-assign]
    registry._resolve_cwd = lambda pid: str(cwd)  # type: ignore[method-assign]

    ctx = registry.context_for_peer("127.0.0.1", 54000)

    assert ctx.session_id.startswith("proxy-4242-")
    assert ctx.client_pid == 4242
    assert ctx.client_cwd == str(cwd)
    assert ctx.proxy_pid == 777
    assert ctx.proxy_port == 8742
    assert ctx.concurrent_sessions == 1
    assert ctx.lock_path.exists()
    payload = json.loads(ctx.lock_path.read_text(encoding="utf-8"))
    assert payload["session_id"] == ctx.session_id
    assert payload["client_pid"] == 4242
    assert payload["proxy_port"] == 8742
    assert ctx.log_path.name == "requests.jsonl"
    assert ctx.decisions_path.name == "decisions.jsonl"


def test_stale_session_cleanup_removes_dead_lockfiles(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    proc_tcp_path = tmp_path / "proc_tcp"
    proc_tcp_path.write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n",
        encoding="utf-8",
    )

    registry = SessionRegistry(
        state_dir=tmp_path / "state",
        proxy_port=8742,
        proc_root=proc_root,
        proc_tcp_path=proc_tcp_path,
    )

    dead_dir = tmp_path / "state" / "sessions" / "dead-session"
    dead_dir.mkdir(parents=True)
    lock_path = dead_dir / "session.lock"
    lock_path.write_text(
        json.dumps(
            {
                "session_id": "dead-session",
                "client_pid": 99999,
                "proxy_pid": 555,
                "proxy_port": 8742,
            }
        ),
        encoding="utf-8",
    )

    registry._last_reap_at = 0.0
    registry._reap_stale_sessions()

    assert not lock_path.exists()


def test_resolve_client_pid_prefers_ss_client_side_match(tmp_path: Path, monkeypatch) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    proc_tcp_path = tmp_path / "proc_tcp"
    proc_tcp_path.write_text("", encoding="utf-8")

    registry = SessionRegistry(
        state_dir=tmp_path / "state",
        proxy_port=8742,
        proc_root=proc_root,
        proc_tcp_path=proc_tcp_path,
    )

    ss_stdout = "\n".join(
        [
            "State Recv-Q Send-Q Local Address:Port  Peer Address:Port Process",
            'ESTAB 0 0 127.0.0.1:54000 127.0.0.1:8742 users:(("python",pid=4242,fd=3))',
            'ESTAB 0 0 127.0.0.1:8742 127.0.0.1:54000 users:(("python",pid=777,fd=4))',
        ]
    )

    def _fake_run(*args, **kwargs):
        return CompletedProcess(args=args, returncode=0, stdout=ss_stdout, stderr="")

    monkeypatch.setattr("tcp.proxy.session_registry.subprocess.run", _fake_run)

    pid = registry._resolve_client_pid("127.0.0.1", 54000)

    assert pid == 4242
