from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from app.config import PROJECT_ROOT, settings
from app.ecommerce import ensure_commerce
from app.store import (
    add_event,
    get_profile,
    list_profiles,
    profile_port_file,
    profile_runner_file,
    update_profile,
    utc_now,
)


def ensure_camoufox_available() -> dict[str, Any]:
    candidates: list[Path] = []
    try:
        from camoufox.utils import launch_path
        candidates.append(Path(launch_path()))
    except Exception:
        pass
    candidates.append(settings.camoufox_macos_app_abs)

    for candidate in candidates:
        if candidate.exists():
            return {"ok": True, "path": str(candidate), "fetched": False}

    result = subprocess.run(
        [sys.executable, "-m", "camoufox", "fetch"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr[-1200:] or result.stdout[-1200:]}
    return {"ok": True, "path": "camoufox fetch", "fetched": True}


def sync_runtime(profile: dict[str, Any]) -> dict[str, Any]:
    pid = profile.get("process_pid")
    if profile.get("status") == "running" and pid and not psutil.pid_exists(int(pid)):
        profile = update_profile(
            profile["id"],
            status="stopped",
            process_pid=None,
            command_port=None,
            last_close_at=utc_now(),
        )
        add_event("runtime", profile["id"], "process disappeared", {"pid": pid}, level="warning")
    elif profile.get("status") == "running" and pid:
        runtime_port = _active_runner_port(profile["id"], int(pid))
        if runtime_port and int(profile.get("command_port") or 0) != runtime_port:
            profile = update_profile(profile["id"], command_port=runtime_port)
            add_event(
                "runtime",
                profile["id"],
                "command port refreshed",
                {"pid": pid, "port": runtime_port},
                level="warning",
            )
    return profile


def running_count() -> int:
    count = 0
    for profile in list_profiles():
        profile = sync_runtime(profile)
        if profile.get("status") == "running":
            count += 1
    return count


def launch_profile(profile_id: str, start_url: str | None = None, headless: bool = False) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    profile = sync_runtime(profile)
    if profile.get("status") == "running":
        return {"ok": True, "already_running": True, "profile": _public_profile(profile)}
    if running_count() >= settings.max_concurrent_launches:
        return {"ok": False, "error": "max_concurrent_launches_reached"}

    available = ensure_camoufox_available()
    if not available.get("ok"):
        update_profile(profile_id, last_error=available.get("error", "camoufox unavailable"))
        return available

    url = start_url or profile.get("start_url") or settings.default_start_url
    log_path = settings.logs_root_abs / f"{profile_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for stale_file in (profile_port_file(profile_id), profile_runner_file(profile_id)):
        try:
            stale_file.unlink()
        except FileNotFoundError:
            pass
    log_file = log_path.open("a", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "app.runner",
        "--profile-id",
        profile_id,
        "--start-url",
        url,
    ]
    if headless:
        cmd.append("--headless")

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=os.environ.copy(),
    )
    log_file.close()

    port = _wait_for_port(profile_id, proc, log_path)
    if not port:
        tail = _tail(log_path)
        update_profile(profile_id, status="stopped", process_pid=None, command_port=None, last_error=tail)
        add_event("runtime", profile_id, "launch failed", {"tail": tail}, level="error")
        return {"ok": False, "error": "launch_failed", "tail": tail}

    updated = update_profile(
        profile_id,
        status="running",
        process_pid=proc.pid,
        command_port=port,
        start_url=url,
        last_launch_at=utc_now(),
        launch_count=int(profile.get("launch_count") or 0) + 1,
        last_error="",
    )
    add_event("runtime", profile_id, "launched", {"pid": proc.pid, "port": port, "start_url": url})
    return {"ok": True, "profile": _public_profile(updated), "log": str(log_path)}


def _wait_for_port(profile_id: str, proc: subprocess.Popen, log_path: Path) -> int | None:
    port_file = profile_port_file(profile_id)
    for _ in range(90):
        if proc.poll() is not None:
            return None
        if port_file.exists():
            try:
                port = int(port_file.read_text(encoding="utf-8").strip())
                runner_port = _active_runner_port(profile_id, proc.pid)
                if runner_port == port and _port_accepts(port):
                    return port
            except ValueError:
                pass
        time.sleep(0.25)
    return None


def stop_profile(profile_id: str, force: bool = False) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    pid = profile.get("process_pid")
    if not pid or not psutil.pid_exists(int(pid)):
        updated = update_profile(profile_id, status="stopped", process_pid=None, command_port=None, last_close_at=utc_now())
        return {"ok": True, "already_stopped": True, "profile": _public_profile(updated)}

    proc = psutil.Process(int(pid))
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except psutil.TimeoutExpired:
        if force:
            proc.kill()
            proc.wait(timeout=5)
        else:
            proc.kill()
            proc.wait(timeout=5)

    updated = update_profile(profile_id, status="stopped", process_pid=None, command_port=None, last_close_at=utc_now())
    add_event("runtime", profile_id, "stopped", {"pid": pid})
    return {"ok": True, "profile": _public_profile(updated)}


def send_command(profile_id: str, command: dict[str, Any]) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise KeyError(profile_id)
    profile = sync_runtime(profile)
    if profile.get("status") != "running":
        return {"ok": False, "error": "profile_not_running"}

    ports = _candidate_ports(profile_id, profile)
    if not ports:
        return {"ok": False, "error": "command_port_missing"}

    command = {**command, "profile_id": profile_id}
    payload = json.dumps(command).encode("utf-8")
    errors: list[str] = []
    for port in ports:
        try:
            reply = _send_payload(port, payload)
            if int(profile.get("command_port") or 0) != port:
                update_profile(profile_id, command_port=port, last_error="")
            add_event("command", profile_id, command.get("action", "command"), {"reply_ok": reply.get("ok", False)})
            return reply
        except OSError as exc:
            errors.append(f"{port}: {exc}")

    add_event(
        "runtime",
        profile_id,
        "command channel unreachable",
        {"ports": ports, "errors": errors[-3:]},
        level="warning",
    )
    update_profile(profile_id, last_error="command_channel_unreachable")
    return {"ok": False, "error": "command_channel_unreachable", "ports": ports, "detail": errors[-3:]}


def _send_payload(port: int, payload: bytes) -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", int(port)), timeout=10) as conn:
        conn.sendall(struct.pack("<I", len(payload)) + payload)
        header = _recv_exact(conn, 4)
        body_len = struct.unpack("<I", header)[0]
        body = _recv_exact(conn, body_len)
    return json.loads(body.decode("utf-8"))


def _candidate_ports(profile_id: str, profile: dict[str, Any]) -> list[int]:
    ports: list[int] = []
    for port in (
        profile.get("command_port"),
        _active_runner_port(profile_id, int(profile.get("process_pid") or 0)),
        _read_port_file(profile_id),
    ):
        try:
            value = int(port or 0)
        except (TypeError, ValueError):
            continue
        if value and value not in ports:
            ports.append(value)
    return ports


def _active_runner_port(profile_id: str, pid: int) -> int | None:
    path = profile_runner_file(profile_id)
    if not path.exists():
        return None
    try:
        runner = json.loads(path.read_text(encoding="utf-8"))
        if pid and int(runner.get("pid") or 0) != pid:
            return None
        if runner.get("status") != "running":
            return None
        return int(runner.get("port") or 0) or None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _read_port_file(profile_id: str) -> int | None:
    path = profile_port_file(profile_id)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _port_accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return True
    except OSError:
        return False


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        if not chunk:
            raise OSError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data[-limit:]


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(ensure_commerce(profile)))
    proxy = clean.get("proxy") or {}
    if proxy.get("password"):
        proxy["password"] = "***"
    if proxy.get("username"):
        proxy["username"] = "***"
    return clean


def public_profiles() -> list[dict[str, Any]]:
    return [_public_profile(sync_runtime(profile)) for profile in list_profiles()]


def log_tail(profile_id: str) -> dict[str, Any]:
    path = settings.logs_root_abs / f"{profile_id}.log"
    return {"ok": True, "path": str(path), "tail": _tail(path)}
