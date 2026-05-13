from __future__ import annotations

import json
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.camoufox_core import (
    launch_profile,
    log_tail,
    public_profiles,
    send_command,
    stop_profile,
)
from app.camoufox_fleet_io import DEFAULT_FLEET_SOURCE, export_system_archive, import_camoufox_fleet
from app.config import PROJECT_ROOT, ensure_directories, settings
from app.ecommerce import ensure_commerce, find_task, migrate_ecommerce_profiles, task_catalog
from app.proxy_check import check_proxy
from app.store import add_event, delete_profile, get_profile, load_state, save_state, update_profile, upsert_profile, utc_now


ensure_directories()
migrate_ecommerce_profiles()
app = FastAPI(title="极恩跨境指纹浏览器")
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="frontend-assets")


class LaunchBody(BaseModel):
    start_url: str | None = None
    headless: bool = False


class NavigateBody(BaseModel):
    url: str


class CommandBody(BaseModel):
    action: str
    params: dict[str, Any] = {}


class ProfileBody(BaseModel):
    id: str
    name: str
    tags: list[str] = []
    start_url: str = settings.default_start_url
    proxy: dict[str, Any] = {"mode": "direct", "display": "Direct"}
    commerce: dict[str, Any] = {}
    fingerprint: dict[str, Any] = {
        "timezone": "America/Los_Angeles",
        "locale": "en-US",
        "screen": {"width": 1512, "height": 982},
    }


class CommerceBody(BaseModel):
    platform: str | None = None
    brand: str | None = None
    market: str | None = None
    owner: str | None = None
    account_status: str | None = None
    priority: str | None = None
    daily_goal: str | None = None
    notes: str | None = None


class TaskBody(BaseModel):
    task_id: str


class BatchBody(BaseModel):
    ids: list[str]
    action: str
    task_id: str | None = None
    start_url: str | None = None


class CamoufoxFleetImportBody(BaseModel):
    source_dir: str = DEFAULT_FLEET_SOURCE
    copy_browser_data: bool = True
    overwrite_browser_data: bool = False
    include_cache: bool = False


def _redact_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    clean = json.loads(json.dumps(profile))
    proxy = clean.get("proxy") or {}
    if proxy.get("username"):
        proxy["username"] = "***"
    if proxy.get("password"):
        proxy["password"] = "***"
    return clean


def _check_profile_proxy(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    check = check_proxy(profile.get("proxy") or {"mode": "direct"})
    if not check.get("ok") and profile.get("status") == "running":
        browser_check = _check_proxy_with_running_browser(profile)
        if browser_check.get("ok"):
            check = browser_check
    proxy = {**(profile.get("proxy") or {})}
    proxy["last_check"] = {**check, "checked_at": utc_now()}
    profile["proxy"] = proxy
    profile = ensure_commerce(profile)
    upsert_profile(profile)
    return profile, check


def _check_proxy_with_running_browser(profile: dict[str, Any]) -> dict[str, Any]:
    original_index = 0
    temp_index: int | None = None
    try:
        pages = send_command(profile["id"], {"action": "pages"})
        if pages.get("ok"):
            original_index = int(pages.get("current_index") or 0)
        opened = send_command(profile["id"], {"action": "new_page", "url": "https://ipwho.is/"})
        if not opened.get("ok"):
            return {"ok": False, "error": opened.get("error", "browser_ip_check_failed")}
        temp_index = int(opened.get("index", opened.get("page_count", 1) - 1))
        evaluated = send_command(
            profile["id"],
            {
                "action": "evaluate",
                "script": "() => document.body ? document.body.innerText : ''",
            },
        )
        if not evaluated.get("ok"):
            return {"ok": False, "error": evaluated.get("error", "browser_ip_eval_failed")}
        raw_text = str(evaluated.get("value") or "").strip()
        data = json.loads(raw_text)
        connection = data.get("connection") or {}
        return {
            "ok": bool(data.get("ip")),
            "ip": data.get("ip", ""),
            "country": data.get("country_code", ""),
            "city": data.get("city", ""),
            "org": connection.get("org") or connection.get("isp") or "",
            "timezone": (data.get("timezone") or {}).get("id", ""),
            "provider": "ipwho.is/browser",
            "raw": data,
        }
    except Exception as exc:
        return {"ok": False, "error": f"browser_ip_check_failed: {str(exc)[:240]}"}
    finally:
        if temp_index is not None:
            try:
                send_command(profile["id"], {"action": "close_page", "index": temp_index, "switch_to": original_index})
            except Exception:
                pass


def _deep_check_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("status") != "running":
        return {"ok": False, "error": "profile_not_running"}

    env_result = send_command(
        profile["id"],
        {
            "action": "evaluate",
            "script": """() => ({
                url: location.href,
                title: document.title,
                readyState: document.readyState,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                localTime: new Date().toString(),
                language: navigator.language,
                languages: navigator.languages,
                platform: navigator.platform,
                userAgent: navigator.userAgent,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory || null,
                webdriver: navigator.webdriver === true,
                doNotTrack: navigator.doNotTrack || null,
                globalPrivacyControl: navigator.globalPrivacyControl === true,
                cookieEnabled: navigator.cookieEnabled,
                screen: {
                    width: screen.width,
                    height: screen.height,
                    availWidth: screen.availWidth,
                    availHeight: screen.availHeight,
                    colorDepth: screen.colorDepth,
                    pixelDepth: screen.pixelDepth
                },
                viewport: { width: innerWidth, height: innerHeight }
            })""",
        },
    )
    if not env_result.get("ok"):
        return {"ok": False, "error": env_result.get("error", "environment_eval_failed")}

    proxy_check = (profile.get("proxy") or {}).get("last_check") or {}
    if not proxy_check.get("ok"):
        profile, proxy_check = _check_profile_proxy(profile)

    env = env_result.get("value") or {}
    fp = profile.get("fingerprint") or {}
    commerce = profile.get("commerce") or {}
    issues: list[dict[str, Any]] = []

    def add_issue(level: str, label: str, detail: str, penalty: int) -> None:
        issues.append({"level": level, "label": label, "detail": detail, "penalty": penalty})

    if env.get("webdriver"):
        add_issue("risk", "自动化标记暴露", "navigator.webdriver 为 true", 40)
    if not proxy_check.get("ok"):
        add_issue("risk", "代理检测失败", str(proxy_check.get("error") or "无法获取代理出口"), 30)

    proxy_tz = str(proxy_check.get("timezone") or "")
    browser_tz = str(env.get("timezone") or "")
    if proxy_tz and browser_tz and proxy_tz != browser_tz:
        add_issue("risk", "时区不一致", f"代理 {proxy_tz} / 浏览器 {browser_tz}", 30)

    expected_country = str((commerce.get("market") or "")).upper()
    actual_country = str(proxy_check.get("country") or "").upper()
    if expected_country == "UK":
        expected_country = "GB"
    if expected_country and actual_country and expected_country != actual_country:
        add_issue("risk", "市场国家不一致", f"市场 {commerce.get('market')} / 代理 {actual_country}", 28)

    language = str(env.get("language") or "")
    if actual_country == "US" and language and not language.lower().startswith("en"):
        add_issue("warn", "语言不匹配", f"美国 IP 但浏览器语言为 {language}", 12)

    if str(fp.get("platform") or "") and env.get("platform") and fp.get("platform") != env.get("platform"):
        add_issue("warn", "平台字段不一致", f"配置 {fp.get('platform')} / 浏览器 {env.get('platform')}", 10)

    configured_screen = fp.get("screen") or {}
    actual_screen = env.get("screen") or {}
    if configured_screen and actual_screen:
        width_delta = abs(int(configured_screen.get("width") or 0) - int(actual_screen.get("width") or 0))
        height_delta = abs(int(configured_screen.get("height") or 0) - int(actual_screen.get("height") or 0))
        if width_delta > 220 or height_delta > 220:
            add_issue(
                "warn",
                "屏幕尺寸漂移",
                f"配置 {configured_screen.get('width')}x{configured_screen.get('height')} / 浏览器 {actual_screen.get('width')}x{actual_screen.get('height')}",
                8,
            )

    configured_ua = str(fp.get("user_agent") or "")
    browser_ua = str(env.get("userAgent") or "")
    if configured_ua and browser_ua and configured_ua != browser_ua:
        add_issue("info", "UA 配置未参与运行", "当前运行 UA 由 Camoufox 内核生成，和原始 UA 不同", 0)

    score = max(0, 100 - sum(int(issue["penalty"]) for issue in issues))
    result = {
        "ok": True,
        "score": score,
        "level": "good" if score >= 85 else "warn" if score >= 60 else "risk",
        "checked_at": utc_now(),
        "issues": issues,
        "proxy": {
            "ip": proxy_check.get("ip", ""),
            "country": proxy_check.get("country", ""),
            "city": proxy_check.get("city", ""),
            "org": proxy_check.get("org", ""),
            "timezone": proxy_check.get("timezone", ""),
            "provider": proxy_check.get("provider", ""),
        },
        "browser": env,
    }
    profile["environment"] = {"last_check": result}
    profile = ensure_commerce(profile)
    upsert_profile(profile)
    add_event("environment", profile["id"], "deep check", {"score": score, "level": result["level"]})
    return result


def _calibrate_profile_environment(profile: dict[str, Any]) -> dict[str, Any]:
    first_check = _deep_check_profile(profile)
    if not first_check.get("ok"):
        return first_check

    latest = get_profile(profile["id"]) or profile
    fp = {**(latest.get("fingerprint") or {})}
    browser = first_check.get("browser") or {}
    changed: dict[str, Any] = {}

    screen = browser.get("screen") or {}
    width = screen.get("width")
    height = screen.get("height")
    if width and height:
        next_screen = {"width": int(width), "height": int(height)}
        if fp.get("screen") != next_screen:
            changed["screen"] = {"from": fp.get("screen"), "to": next_screen}
            fp["screen"] = next_screen

    timezone_id = browser.get("timezone")
    if timezone_id and fp.get("timezone") != timezone_id:
        changed["timezone"] = {"from": fp.get("timezone"), "to": timezone_id}
        fp["timezone"] = timezone_id

    language = browser.get("language")
    if language and fp.get("locale") != language:
        changed["locale"] = {"from": fp.get("locale"), "to": language}
        fp["locale"] = language

    platform = browser.get("platform")
    if platform and fp.get("platform") != platform:
        changed["platform"] = {"from": fp.get("platform"), "to": platform}
        fp["platform"] = platform

    latest["fingerprint"] = fp
    latest = ensure_commerce(latest)
    upsert_profile(latest)
    verified = _deep_check_profile(get_profile(profile["id"]) or latest)
    add_event("environment", profile["id"], "environment calibrated", {"changed": list(changed)})
    return {
        "ok": True,
        "changed": changed,
        "check": verified,
        "restart_required": False,
        "profile": _redact_profile(get_profile(profile["id"])),
    }


def _sync_timezone_from_proxy(profile: dict[str, Any], force_check: bool = False) -> dict[str, Any]:
    check = (profile.get("proxy") or {}).get("last_check") or {}
    if force_check or not check.get("timezone"):
        profile, check = _check_profile_proxy(profile)
    timezone_id = str(check.get("timezone") or "")
    if not check.get("ok"):
        return {"ok": False, "error": "proxy_check_failed", "check": check}
    if not timezone_id:
        return {"ok": False, "error": "timezone_missing", "check": check}

    fingerprint = {**(profile.get("fingerprint") or {})}
    old_timezone = fingerprint.get("timezone")
    fingerprint["timezone"] = timezone_id
    profile["fingerprint"] = fingerprint
    profile = ensure_commerce(profile)
    upsert_profile(profile)
    add_event(
        "profile",
        profile["id"],
        "timezone synced",
        {"old_timezone": old_timezone, "timezone": timezone_id, "ip": check.get("ip")},
    )
    return {
        "ok": True,
        "timezone": timezone_id,
        "old_timezone": old_timezone,
        "restart_required": profile.get("status") == "running",
        "check": check,
        "profile": _redact_profile(profile),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    dist_index = FRONTEND_DIST / "index.html"
    if dist_index.exists():
        return dist_index.read_text(encoding="utf-8")
    return (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    icon = FRONTEND_DIST / "favicon.svg"
    if not icon.exists():
        raise HTTPException(404, "favicon_not_found")
    return FileResponse(icon)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "jien-cross-border-fingerprint-browser", "port": settings.port}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    profiles = public_profiles()
    state = load_state()
    return {
        "profiles_total": len(profiles),
        "profiles_running": len([p for p in profiles if p.get("status") == "running"]),
        "risk_total": len([p for p in profiles if (p.get("health") or {}).get("level") == "risk"]),
        "warn_total": len([p for p in profiles if (p.get("health") or {}).get("level") == "warn"]),
        "events_total": len(state.get("events", [])),
        "max_concurrent_launches": settings.max_concurrent_launches,
    }


@app.post("/api/import/camoufox-fleet")
def api_import_camoufox_fleet(body: CamoufoxFleetImportBody) -> dict[str, Any]:
    try:
        result = import_camoufox_fleet(
            body.source_dir,
            copy_browser_data=body.copy_browser_data,
            overwrite_browser_data=body.overwrite_browser_data,
            include_cache=body.include_cache,
        )
        add_event("import", None, "camoufox fleet imported", {"count": result["count"], "source": result["source"]})
        return result
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/export/archive")
def api_export_archive(
    include_browser_data: bool = True,
    include_logs: bool = False,
    include_screenshots: bool = False,
    include_cache: bool = False,
) -> FileResponse:
    try:
        result = export_system_archive(
            include_browser_data=include_browser_data,
            include_logs=include_logs,
            include_screenshots=include_screenshots,
            include_cache=include_cache,
        )
        add_event("export", None, "data archive exported", {"file": result["filename"], "bytes": result["bytes"]})
        return FileResponse(result["path"], media_type="application/zip", filename=result["filename"])
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/profiles")
def api_profiles() -> list[dict[str, Any]]:
    return public_profiles()


@app.post("/api/profiles")
def api_upsert_profile(body: ProfileBody) -> dict[str, Any]:
    profile = body.model_dump()
    profile.setdefault("source", "manual")
    profile.setdefault("status", "stopped")
    profile.setdefault("process_pid", None)
    profile.setdefault("command_port", None)
    profile.setdefault("created_at", utc_now())
    profile.setdefault("updated_at", utc_now())
    profile.setdefault("launch_count", 0)
    profile = ensure_commerce(profile)
    upsert_profile(profile)
    add_event("profile", profile["id"], "saved")
    return {"ok": True, "profile": _redact_profile(get_profile(profile["id"]))}


@app.patch("/api/profiles/{profile_id}/commerce")
def api_update_commerce(profile_id: str, body: CommerceBody) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    commerce = {**(profile.get("commerce") or {})}
    for key, value in body.model_dump(exclude_none=True).items():
        commerce[key] = value
    profile["commerce"] = commerce
    profile = ensure_commerce(profile)
    upsert_profile(profile)
    add_event("profile", profile_id, "commerce updated", {"commerce": commerce})
    return {"ok": True, "profile": _redact_profile(profile)}


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    if profile.get("status") == "running":
        stop_profile(profile_id, force=True)
    return {"ok": delete_profile(profile_id)}


@app.post("/api/profiles/{profile_id}/launch")
def api_launch(profile_id: str, body: LaunchBody) -> dict[str, Any]:
    try:
        result = launch_profile(profile_id, body.start_url, body.headless)
        if not result.get("ok"):
            raise HTTPException(400, result)
        return result
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.post("/api/profiles/{profile_id}/stop")
def api_stop(profile_id: str) -> dict[str, Any]:
    try:
        return stop_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.post("/api/profiles/{profile_id}/navigate")
def api_navigate(profile_id: str, body: NavigateBody) -> dict[str, Any]:
    try:
        result = send_command(profile_id, {"action": "navigate", "url": body.url})
        if result.get("ok"):
            update_profile(profile_id, start_url=body.url)
        return result
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.post("/api/profiles/{profile_id}/task")
def api_run_task(profile_id: str, body: TaskBody) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    platform = (profile.get("commerce") or {}).get("platform")
    task = find_task(body.task_id, platform)
    if not task:
        raise HTTPException(404, "task_not_found")
    if profile.get("status") == "running":
        result = send_command(profile_id, {"action": "navigate", "url": task["url"]})
    else:
        result = launch_profile(profile_id, task["url"])
    add_event("task", profile_id, task["label"], {"task_id": task["id"], "url": task["url"], "ok": result.get("ok")})
    return {"ok": result.get("ok", False), "task": task, "result": result}


@app.post("/api/profiles/batch")
def api_batch(body: BatchBody) -> dict[str, Any]:
    results = []
    for profile_id in body.ids:
        try:
            if body.action == "launch":
                result = launch_profile(profile_id, body.start_url)
            elif body.action == "stop":
                result = stop_profile(profile_id)
            elif body.action == "proxy":
                profile = get_profile(profile_id)
                if not profile:
                    raise KeyError(profile_id)
                profile, check = _check_profile_proxy(profile)
                result = check
            elif body.action == "sync_timezone":
                profile = get_profile(profile_id)
                if not profile:
                    raise KeyError(profile_id)
                result = _sync_timezone_from_proxy(profile)
            elif body.action == "deep_check":
                profile = get_profile(profile_id)
                if not profile:
                    raise KeyError(profile_id)
                result = _deep_check_profile(profile)
            elif body.action == "calibrate_environment":
                profile = get_profile(profile_id)
                if not profile:
                    raise KeyError(profile_id)
                result = _calibrate_profile_environment(profile)
            elif body.action == "task" and body.task_id:
                result = api_run_task(profile_id, TaskBody(task_id=body.task_id))
            else:
                result = {"ok": False, "error": "unsupported_batch_action"}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        results.append({"id": profile_id, "result": result})
    add_event("batch", None, body.action, {"count": len(body.ids), "task_id": body.task_id})
    return {"ok": True, "results": results}


@app.post("/api/profiles/{profile_id}/screenshot")
def api_screenshot(profile_id: str) -> dict[str, Any]:
    try:
        return send_command(profile_id, {"action": "screenshot", "full_page": False})
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.get("/api/profiles/{profile_id}/pages")
def api_pages(profile_id: str) -> dict[str, Any]:
    try:
        return send_command(profile_id, {"action": "pages"})
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.post("/api/profiles/{profile_id}/command")
def api_command(profile_id: str, body: CommandBody) -> dict[str, Any]:
    payload = {"action": body.action, **body.params}
    try:
        return send_command(profile_id, payload)
    except KeyError as exc:
        raise HTTPException(404, "profile_not_found") from exc


@app.post("/api/profiles/{profile_id}/proxy-check")
def api_proxy_check(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    profile, result = _check_profile_proxy(profile)
    add_event("proxy", profile_id, "proxy checked", {"ok": result.get("ok")})
    return result


@app.post("/api/profiles/{profile_id}/sync-timezone")
def api_sync_timezone(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    result = _sync_timezone_from_proxy(profile)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@app.post("/api/profiles/{profile_id}/deep-check")
def api_deep_check(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    result = _deep_check_profile(profile)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@app.post("/api/profiles/{profile_id}/calibrate-environment")
def api_calibrate_environment(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(404, "profile_not_found")
    result = _calibrate_profile_environment(profile)
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@app.get("/api/commerce/tasks")
def api_commerce_tasks() -> list[dict[str, Any]]:
    return task_catalog()


@app.get("/api/events")
def api_events() -> list[dict[str, Any]]:
    return list(reversed(load_state().get("events", [])[-100:]))


@app.get("/api/profiles/{profile_id}/logs", response_class=PlainTextResponse)
def api_logs(profile_id: str) -> str:
    return log_tail(profile_id)["tail"]


@app.post("/api/state/compact")
def compact_state() -> dict[str, Any]:
    state = load_state()
    del state.setdefault("events", [])[:-200]
    save_state(state)
    return {"ok": True, "events": len(state.get("events", []))}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
