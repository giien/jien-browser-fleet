from __future__ import annotations

import argparse
import json
import signal
import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from browserforge.fingerprints import Screen
from playwright.sync_api import sync_playwright

from app.config import settings
from app.socks5_bridge import Socks5AuthBridge
from app.store import (
    get_profile,
    profile_browser_dir,
    profile_port_file,
    profile_runner_file,
    update_profile,
    utc_now,
)


stop_requested = False
page = None
context = None
cmd_queue: deque[tuple[dict[str, Any], str]] = deque()
result_ready = threading.Event()
result: dict[str, Any] | None = None


def _write_runner_state(profile_id: str, **values: Any) -> None:
    state = {
        "profile_id": profile_id,
        "pid": __import__("os").getpid(),
        "updated_at": utc_now(),
        **values,
    }
    profile_runner_file(profile_id).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _command_server(profile_id: str, port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(0.5)
    server.bind(("127.0.0.1", port))
    server.listen(8)

    while not stop_requested:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            header = _recv_exact(conn, 4)
            body_len = struct.unpack("<I", header)[0]
            body = _recv_exact(conn, body_len)
            cmd = json.loads(body.decode("utf-8"))
            reply = _handle_command(cmd)
        except Exception as exc:
            reply = {"ok": False, "error": str(exc)}
        finally:
            try:
                payload = json.dumps(reply).encode("utf-8")
                conn.sendall(struct.pack("<I", len(payload)) + payload)
            except Exception:
                pass
            conn.close()

    server.close()
    try:
        profile_port_file(profile_id).unlink()
    except OSError:
        pass


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        if not chunk:
            raise OSError("connection closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _handle_command(cmd: dict[str, Any]) -> dict[str, Any]:
    global result
    if page is None:
        return {"ok": False, "error": "browser_not_ready"}
    action = str(cmd.get("action", ""))
    cmd_queue.clear()
    result_ready.clear()
    result = None
    cmd_queue.append((cmd, action))
    if not result_ready.wait(timeout=settings.command_timeout_seconds):
        return {"ok": False, "error": "command_timeout"}
    return result or {"ok": False, "error": "empty_result"}


def _process_command(cmd: dict[str, Any], action: str) -> dict[str, Any]:
    global page
    try:
        if action == "navigate":
            url = cmd.get("url") or "about:blank"
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return {"ok": True, "url": page.url, "title": page.title()}
        if action == "title":
            return {"ok": True, "url": page.url, "title": page.title()}
        if action == "screenshot":
            path = cmd.get("path")
            if not path:
                stamp = int(time.time() * 1000)
                path = str(settings.screenshots_root_abs / f"{cmd.get('profile_id', 'profile')}-{stamp}.png")
            page.screenshot(path=path, full_page=bool(cmd.get("full_page", False)))
            return {"ok": True, "path": path, "url": page.url, "title": page.title()}
        if action == "evaluate":
            return {"ok": True, "value": page.evaluate(str(cmd["script"]))}
        if action == "click":
            page.click(str(cmd["selector"]), timeout=10000)
            return {"ok": True}
        if action == "fill":
            page.fill(str(cmd["selector"]), str(cmd.get("value", "")), timeout=10000)
            return {"ok": True}
        if action == "scroll":
            page.mouse.wheel(int(cmd.get("dx", 0)), int(cmd.get("dy", 500)))
            return {"ok": True}
        if action == "new_page":
            new_page = context.new_page()
            new_page.goto(cmd.get("url") or "about:blank", wait_until="domcontentloaded", timeout=30000)
            page = new_page
            pages = context.pages
            return {
                "ok": True,
                "url": page.url,
                "title": page.title(),
                "index": pages.index(page),
                "page_count": len(pages),
            }
        if action == "switch_page":
            index = int(cmd.get("index", 0))
            pages = context.pages
            if not 0 <= index < len(pages):
                return {"ok": False, "error": "page_index_out_of_range", "page_count": len(pages)}
            page = pages[index]
            return {"ok": True, "url": page.url, "title": page.title(), "page_count": len(pages)}
        if action == "close_page":
            pages = context.pages
            index = int(cmd.get("index", pages.index(page)))
            if not 0 <= index < len(pages):
                return {"ok": False, "error": "page_index_out_of_range", "page_count": len(pages)}
            target = pages[index]
            closing_active = target == page
            if len(pages) == 1:
                target.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
                page = target
                return {"ok": True, "url": page.url, "title": page.title(), "page_count": 1, "current_index": 0}
            target.close()
            pages = context.pages
            if closing_active:
                preferred = min(int(cmd.get("switch_to", max(index - 1, 0))), len(pages) - 1)
                page = pages[preferred]
            return {
                "ok": True,
                "url": page.url,
                "title": page.title(),
                "page_count": len(pages),
                "current_index": pages.index(page),
            }
        if action == "pages":
            return {
                "ok": True,
                "current_index": context.pages.index(page),
                "pages": [{"index": i, "url": p.url, "title": p.title()} for i, p in enumerate(context.pages)],
            }
        return {"ok": False, "error": f"unknown_action:{action}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _handle_stop(signum, frame) -> None:
    global stop_requested
    stop_requested = True


def _proxy_config(profile: dict[str, Any]) -> tuple[dict[str, Any] | None, Socks5AuthBridge | None]:
    proxy = profile.get("proxy") or {}
    if proxy.get("mode") != "proxy":
        return None, None

    protocol = proxy.get("protocol") or "socks5"
    host = proxy.get("host")
    port = proxy.get("port")
    username = proxy.get("username") or ""
    password = proxy.get("password") or ""
    if protocol == "socks5" and username and password:
        bridge = Socks5AuthBridge(host, int(port), username, password)
        local_port = bridge.start()
        return {"server": f"socks5://127.0.0.1:{local_port}"}, bridge

    config = {"server": f"{protocol}://{host}:{port}"}
    if username:
        config["username"] = username
        config["password"] = password
    return config, None


def _launch_options(profile: dict[str, Any], headless: bool) -> dict[str, Any]:
    from camoufox.utils import launch_options as camoufox_launch_options

    fp = profile.get("fingerprint") or {}
    screen = fp.get("screen") or {}
    proxy_config, bridge = _proxy_config(profile)
    profile["_bridge"] = bridge

    options = camoufox_launch_options(
        proxy=proxy_config,
        geoip=False,
        locale=fp.get("locale") or "en-US",
        humanize=True,
        os=("macos",),
        screen=Screen(
            min_width=int(screen.get("width") or 1512),
            max_width=int(screen.get("width") or 1512),
            min_height=int(screen.get("height") or 982),
            max_height=int(screen.get("height") or 982),
        ),
        headless=headless,
        firefox_user_prefs={
            "network.proxy.socks_remote_dns": True,
            "browser.sessionstore.resume_from_crash": False,
            "browser.sessionstore.resume_session_once": False,
            "browser.startup.page": 0,
        },
    )
    options.pop("persistent_context", None)
    options.pop("user_data_dir", None)
    options.pop("firefox_args", None)
    args = options.get("args", [])
    options["args"] = [
        arg for arg in args
        if not (str(arg).startswith("--remote-debugging-port") or str(arg).startswith("--devtools"))
    ]
    options["timezone_id"] = fp.get("timezone") or "America/Los_Angeles"
    return options


def main() -> None:
    global page, context, result

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--start-url", default=settings.default_start_url)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    profile = get_profile(args.profile_id)
    if not profile:
        raise SystemExit(f"profile not found: {args.profile_id}")

    runtime_dir = profile_browser_dir(args.profile_id)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    profile_port_file(args.profile_id).write_text(str(port), encoding="utf-8")
    _write_runner_state(args.profile_id, status="starting", port=port)

    threading.Thread(target=_command_server, args=(args.profile_id, port), daemon=True).start()

    bridge = None
    try:
        options = _launch_options(profile, args.headless)
        bridge = profile.pop("_bridge", None)
        with sync_playwright() as playwright:
            context = playwright.firefox.launch_persistent_context(str(runtime_dir), **options)
            page = context.pages[0] if context.pages else context.new_page()
            _write_runner_state(args.profile_id, status="running", port=port, url=page.url)
            if args.start_url:
                page.goto(args.start_url, wait_until="domcontentloaded", timeout=45000)

            while not stop_requested:
                time.sleep(0.2)
                if cmd_queue:
                    cmd, action = cmd_queue.popleft()
                    result = _process_command(cmd, action)
                    _write_runner_state(args.profile_id, status="running", port=port, url=page.url)
                    result_ready.set()

            context.close()
    finally:
        if bridge:
            bridge.close()
        _write_runner_state(args.profile_id, status="stopped")
        try:
            current = get_profile(args.profile_id)
            if current and current.get("process_pid") == __import__("os").getpid():
                update_profile(args.profile_id, status="stopped", process_pid=None, command_port=None, last_close_at=utc_now())
        except Exception:
            pass


if __name__ == "__main__":
    main()
