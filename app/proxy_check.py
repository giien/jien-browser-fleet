from __future__ import annotations

import subprocess
from typing import Any


ENDPOINTS = [
    "https://ipwho.is/",
    "https://api.ip.sb/geoip",
    "https://ipinfo.io/json",
]


def check_proxy(proxy: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    for endpoint in ENDPOINTS:
        result = _check_endpoint(proxy, endpoint)
        if result.get("ok"):
            return result
        errors.append(result.get("error") or result.get("message") or endpoint)
    return {"ok": False, "error": "; ".join(errors[-3:])}


def _check_endpoint(proxy: dict[str, Any], endpoint: str) -> dict[str, Any]:
    cmd = ["curl", "-sS", "--max-time", "18", endpoint]
    if proxy.get("mode") != "direct":
        protocol = proxy.get("protocol") or "socks5"
        host = proxy.get("host") or ""
        port = proxy.get("port") or ""
        user = proxy.get("username") or ""
        password = proxy.get("password") or ""
        auth = f"{user}:{password}@" if user or password else ""
        proxy_url = f"{protocol}://{auth}{host}:{port}"
        cmd = ["curl", "-sS", "--max-time", "22", "--proxy", proxy_url, endpoint]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip()[:300] or f"curl exit {result.returncode}",
            }
        data = __import__("json").loads(result.stdout)
        normalized = _normalize(data, endpoint)
        if normalized.get("ok"):
            return normalized
        return {
            **normalized,
            "raw": data,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def _normalize(data: dict, endpoint: str) -> dict[str, Any]:
    if "ipwho.is" in endpoint:
        if data.get("success") is False:
            return {"ok": False, "error": data.get("message", "ipwho.is failed"), "raw": data}
        connection = data.get("connection") or {}
        return {
            "ok": bool(data.get("ip")),
            "ip": data.get("ip", ""),
            "country": data.get("country_code", ""),
            "city": data.get("city", ""),
            "org": connection.get("org") or connection.get("isp") or "",
            "timezone": (data.get("timezone") or {}).get("id", ""),
            "provider": "ipwho.is",
            "raw": data,
        }
    if "ip.sb" in endpoint:
        return {
            "ok": bool(data.get("ip")),
            "ip": data.get("ip", ""),
            "country": data.get("country_code", ""),
            "city": data.get("city", ""),
            "org": data.get("organization", ""),
            "timezone": data.get("timezone", ""),
            "provider": "api.ip.sb",
            "raw": data,
        }
    error = data.get("error")
    if isinstance(error, dict):
        return {"ok": False, "error": error.get("message") or error.get("title") or "ipinfo error", "raw": data}
    return {
        "ok": bool(data.get("ip")),
        "ip": data.get("ip", ""),
        "country": data.get("country", ""),
        "city": data.get("city", ""),
        "org": data.get("org", ""),
        "timezone": data.get("timezone", ""),
        "provider": "ipinfo.io",
        "raw": data,
    }
