from __future__ import annotations

import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import PROJECT_ROOT, settings
from app.ecommerce import ensure_commerce
from app.store import get_profile, profile_browser_dir, upsert_profile, utc_now


DEFAULT_FLEET_SOURCE = "/Volumes/Rtl9210/camoufox-fleet-local"
EXPORT_ROOT = PROJECT_ROOT / "data" / "exports"
SKIP_FILE_NAMES = {".parentlock", "parent.lock", "lock", ".startup-incomplete"}
SKIP_DIR_NAMES = {"cache2", "startupCache", "safebrowsing", "thumbnails"}


def _resolve_fleet_db(source: str | Path) -> tuple[Path, Path]:
    raw = Path(str(source or DEFAULT_FLEET_SOURCE)).expanduser()
    candidates = [raw] if raw.is_file() else [raw / "data" / "fleet.db", raw / "fleet.db"]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            root = candidate.parent.parent if candidate.parent.name == "data" else candidate.parent
            return root, candidate
    raise FileNotFoundError(f"fleet.db not found under {raw}")


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [str(value)]


def _iso(value: Any) -> str:
    if not value:
        return utc_now()
    text = str(value)
    if "T" in text:
        return text
    return text.replace(" ", "T") + "+00:00"


def _platform_from_os(value: str | None) -> str:
    if str(value or "").lower() == "macos":
        return "MacIntel"
    if str(value or "").lower() == "windows":
        return "Win32"
    return str(value or "MacIntel")


def _process_exists(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _source_browser_dir(fleet_root: Path, row: sqlite3.Row) -> Path | None:
    profile_id = str(row["id"])
    candidates = [
        fleet_root / "data" / "profiles" / profile_id / "browser-data",
        fleet_root / "profiles" / profile_id / "browser-data",
    ]
    raw_data_dir = str(row["data_dir"] or "")
    if raw_data_dir:
        data_path = Path(raw_data_dir).expanduser()
        candidates.append(data_path if data_path.is_absolute() else fleet_root / raw_data_dir)
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _should_skip(path: Path, include_cache: bool) -> bool:
    parts = path.parts
    if any(part.startswith("._") for part in parts):
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    if not include_cache and any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return False


def _copy_browser_data(
    source: Path | None,
    target: Path,
    *,
    overwrite: bool,
    include_cache: bool,
) -> dict[str, Any]:
    if not source:
        return {"copied": False, "reason": "source_browser_data_missing"}
    if not source.exists():
        return {"copied": False, "reason": "source_browser_data_missing", "source": str(source)}

    if target.exists() and any(target.iterdir()):
        if not overwrite:
            return {"copied": False, "reason": "target_browser_data_exists", "target": str(target)}
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=True)
    files = 0
    bytes_copied = 0
    errors: list[str] = []
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        if _should_skip(rel, include_cache):
            continue
        dest = target / rel
        try:
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, dest)
                files += 1
                bytes_copied += item.stat().st_size
        except OSError as exc:
            if len(errors) < 20:
                errors.append(f"{rel}: {exc}")

    _remove_apple_double_files(target)

    return {
        "copied": files > 0,
        "source": str(source),
        "target": str(target),
        "files": files,
        "bytes": bytes_copied,
        "errors": errors,
    }


def _remove_apple_double_files(root: Path) -> None:
    if not root.exists():
        return
    for item in root.rglob("._*"):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            pass


def _proxy_from_row(row: sqlite3.Row) -> dict[str, Any]:
    if not row["proxy_host"] or not row["proxy_port"]:
        return {
            "mode": "direct",
            "protocol": "",
            "host": "",
            "port": None,
            "username": "",
            "password": "",
            "display": "Direct",
            "last_check": {},
        }

    protocol = row["proxy_protocol"] or "socks5"
    display = f"{protocol}://{row['proxy_host']}:{row['proxy_port']}"
    last_check = {
        "ok": row["proxy_status"] == "healthy",
        "ip": row["last_verified_ip"] or "",
        "country": row["last_verified_country"] or row["expected_country"] or "",
        "org": row["last_verified_org"] or "",
        "checked_at": _iso(row["last_verified_at"]) if row["last_verified_at"] else "",
    }
    return {
        "mode": "proxy",
        "protocol": protocol,
        "host": row["proxy_host"],
        "port": int(row["proxy_port"]),
        "username": row["proxy_username"] or "",
        "password": row["proxy_password"] or "",
        "display": display,
        "label": row["proxy_label"] or "",
        "last_check": last_check,
    }


def _health_from_row(row: sqlite3.Row) -> dict[str, Any]:
    risks: list[str] = []
    if row["proxy_status"] and row["proxy_status"] != "healthy":
        risks.append(f"代理状态为 {row['proxy_status']}")
    if row["expected_country"] and row["market"] and str(row["expected_country"]).upper() != str(row["market"]).upper():
        risks.append(f"代理国家 {row['expected_country']} 与市场 {row['market']} 不一致")
    return {
        "score": 100 if not risks else 72,
        "level": "good" if not risks else "warn",
        "risks": risks,
        "proxy_country": row["last_verified_country"] or row["expected_country"] or "",
        "proxy_ip": row["last_verified_ip"] or "",
        "proxy_timezone": row["timezone_declared"] or "",
    }


def _normalize_profile(
    row: sqlite3.Row,
    *,
    fleet_root: Path,
    fleet_db: Path,
    copy_result: dict[str, Any],
    source_browser: Path | None,
) -> dict[str, Any]:
    profile_id = str(row["id"])
    existing = get_profile(profile_id) or {}
    tags = sorted(set(_json_list(row["tags"]) + ["camoufox-fleet"]))
    source_locked = bool((source_browser and (source_browser / ".parentlock").exists()) or _process_exists(row["process_pid"]))
    profile = {
        "id": profile_id,
        "name": row["store_name"] or row["account_id"] or profile_id,
        "source": "camoufox_fleet",
        "source_id": profile_id,
        "tags": tags,
        "status": existing.get("status", "stopped"),
        "process_pid": existing.get("process_pid"),
        "command_port": existing.get("command_port"),
        "start_url": existing.get(
            "start_url",
            "https://www.instagram.com/" if row["platform"] == "social" else settings.default_start_url,
        ),
        "data_dir": str(profile_browser_dir(profile_id)),
        "proxy": _proxy_from_row(row),
        "fingerprint": {
            "timezone": row["timezone_declared"] or "America/Los_Angeles",
            "locale": row["locale_declared"] or "en-US",
            "platform": _platform_from_os(row["fingerprint_os"]),
            "screen": {
                "width": int(row["screen_width"] or 1512),
                "height": int(row["screen_height"] or 982),
            },
            "fingerprint_seed": row["fingerprint_seed"] or "",
            "browser_type": "firefox",
        },
        "commerce": {
            "platform": row["platform"] or "social",
            "brand": row["store_name"] or "",
            "market": row["market"] or "US",
            "owner": "",
            "account_status": row["account_status"] or "normal",
            "priority": "normal",
            "daily_goal": "",
            "notes": row["description"] or "",
        },
        "health": _health_from_row(row),
        "camoufox_fleet": {
            "source_root": str(fleet_root),
            "source_db": str(fleet_db),
            "source_browser_data": str(source_browser) if source_browser else "",
            "source_locked": source_locked,
            "copy_result": copy_result,
            "humanize": bool(row["humanize"]),
            "block_images": bool(row["block_images"]),
        },
        "created_at": existing.get("created_at") or _iso(row["created_at"]),
        "updated_at": utc_now(),
        "last_launch_at": existing.get("last_launch_at") or (_iso(row["last_launch_at"]) if row["last_launch_at"] else None),
        "last_close_at": existing.get("last_close_at") or (_iso(row["last_close_at"]) if row["last_close_at"] else None),
        "launch_count": existing.get("launch_count", int(row["launch_count"] or 0)),
        "last_error": existing.get("last_error", ""),
    }
    return ensure_commerce(profile)


def import_camoufox_fleet(
    source_dir: str | Path = DEFAULT_FLEET_SOURCE,
    *,
    copy_browser_data: bool = True,
    overwrite_browser_data: bool = False,
    include_cache: bool = False,
) -> dict[str, Any]:
    fleet_root, fleet_db = _resolve_fleet_db(source_dir)
    with sqlite3.connect(fleet_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              p.id, p.account_id, p.proxy_id, p.fingerprint_seed, p.fingerprint_os,
              p.screen_width, p.screen_height, p.locale_declared, p.timezone_declared,
              p.data_dir, p.humanize, p.block_images, p.status AS profile_status,
              p.process_pid, p.last_launch_at, p.last_close_at, p.launch_count, p.created_at,
              a.platform, a.market, a.store_name, a.description, a.tags, a.status AS account_status,
              pr.label AS proxy_label, pr.protocol AS proxy_protocol, pr.host AS proxy_host,
              pr.port AS proxy_port, pr.username AS proxy_username,
              pr.password_encrypted AS proxy_password, pr.expected_country, pr.status AS proxy_status,
              pr.last_verified_at, pr.last_verified_ip, pr.last_verified_country, pr.last_verified_org
            FROM profiles p
            LEFT JOIN accounts a ON a.id = p.account_id
            LEFT JOIN proxies pr ON pr.id = p.proxy_id
            ORDER BY p.created_at ASC
            """
        ).fetchall()

    imported: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        profile_id = str(row["id"])
        source_browser = _source_browser_dir(fleet_root, row)
        copy_result = {"copied": False, "reason": "copy_disabled"}
        if copy_browser_data:
            copy_result = _copy_browser_data(
                source_browser,
                profile_browser_dir(profile_id),
                overwrite=overwrite_browser_data,
                include_cache=include_cache,
            )
        profile = _normalize_profile(
            row,
            fleet_root=fleet_root,
            fleet_db=fleet_db,
            copy_result=copy_result,
            source_browser=source_browser,
        )
        upsert_profile(profile)
        if profile["camoufox_fleet"]["source_locked"]:
            warnings.append(f"{profile_id}: source profile appears to be running; copied data may need a fresh import after stopping it")
        if copy_result.get("errors"):
            warnings.append(f"{profile_id}: {len(copy_result['errors'])} files failed to copy")
        imported.append({
            "id": profile["id"],
            "name": profile["name"],
            "platform": profile["commerce"]["platform"],
            "market": profile["commerce"]["market"],
            "browser_data": copy_result,
            "source_locked": profile["camoufox_fleet"]["source_locked"],
        })

    return {
        "ok": True,
        "source": str(fleet_db),
        "count": len(imported),
        "profiles": imported,
        "warnings": warnings,
    }


def _iter_files(root: Path, *, include_cache: bool) -> Iterable[Path]:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and not _should_skip(path.relative_to(root), include_cache):
            yield path


def _zip_tree(zip_file: zipfile.ZipFile, root: Path, arc_root: str, *, include_cache: bool) -> int:
    count = 0
    for path in _iter_files(root, include_cache=include_cache):
        zip_file.write(path, f"{arc_root}/{path.relative_to(root)}")
        count += 1
    return count


def export_system_archive(
    *,
    include_browser_data: bool = True,
    include_logs: bool = False,
    include_screenshots: bool = False,
    include_cache: bool = False,
) -> dict[str, Any]:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"jien-browser-data-export-{stamp}.zip"
    path = EXPORT_ROOT / filename
    state_path = settings.state_file_abs
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    manifest = {
        "app": "极恩跨境指纹浏览器",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "include_browser_data": include_browser_data,
        "include_logs": include_logs,
        "include_screenshots": include_screenshots,
        "include_cache": include_cache,
        "profiles": len(state.get("profiles", [])),
    }

    file_count = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
        zip_file.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        file_count += 1
        if state_path.exists():
            zip_file.write(state_path, "data/state.json")
            file_count += 1
        if include_browser_data:
            for profile in state.get("profiles", []):
                profile_id = str(profile.get("id") or "")
                if not profile_id:
                    continue
                browser_root = settings.data_root_abs / profile_id / "browser-data"
                file_count += _zip_tree(
                    zip_file,
                    browser_root,
                    f"data/profiles/{profile_id}/browser-data",
                    include_cache=include_cache,
                )
        if include_logs:
            file_count += _zip_tree(zip_file, settings.logs_root_abs, "logs", include_cache=True)
        if include_screenshots:
            file_count += _zip_tree(zip_file, settings.screenshots_root_abs, "data/screenshots", include_cache=True)

    return {
        "ok": True,
        "path": str(path),
        "filename": filename,
        "bytes": path.stat().st_size,
        "files": file_count,
    }
