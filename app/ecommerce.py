from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import list_profiles, upsert_profile


PLATFORM_TASKS: dict[str, list[dict[str, str]]] = {
    "tiktok_shop": [
        {"id": "seller_home", "label": "Seller Center", "url": "https://seller-us.tiktok.com/"},
        {"id": "orders", "label": "订单", "url": "https://seller-us.tiktok.com/order"},
        {"id": "products", "label": "商品", "url": "https://seller-us.tiktok.com/product/manage"},
        {"id": "ads", "label": "广告", "url": "https://ads.tiktok.com/i18n/home"},
        {"id": "creator", "label": "达人合作", "url": "https://affiliate.tiktokglobalshop.com/"},
        {"id": "front", "label": "TikTok", "url": "https://www.tiktok.com/"},
    ],
    "amazon": [
        {"id": "seller_home", "label": "Seller Central", "url": "https://sellercentral.amazon.com/"},
        {"id": "orders", "label": "订单", "url": "https://sellercentral.amazon.com/orders-v3"},
        {"id": "inventory", "label": "库存", "url": "https://sellercentral.amazon.com/inventory"},
        {"id": "ads", "label": "广告", "url": "https://advertising.amazon.com/"},
    ],
    "shopify": [
        {"id": "admin", "label": "后台", "url": "https://admin.shopify.com/"},
        {"id": "orders", "label": "订单", "url": "https://admin.shopify.com/store"},
        {"id": "products", "label": "商品", "url": "https://admin.shopify.com/store"},
        {"id": "front", "label": "前台", "url": "https://www.shopify.com/"},
    ],
    "social": [
        {"id": "instagram", "label": "Instagram", "url": "https://www.instagram.com/"},
        {"id": "facebook_business", "label": "Meta Business", "url": "https://business.facebook.com/"},
        {"id": "tiktok", "label": "TikTok", "url": "https://www.tiktok.com/"},
    ],
    "utility": [
        {"id": "check_ip", "label": "检查 IP", "url": "https://ipwho.is/"},
        {"id": "gmail", "label": "邮箱", "url": "https://mail.google.com/"},
    ],
}


MARKET_COUNTRY = {
    "US": "US",
    "UK": "GB",
    "GB": "GB",
    "CA": "CA",
    "AU": "AU",
    "DE": "DE",
    "FR": "FR",
    "IT": "IT",
    "ES": "ES",
    "JP": "JP",
    "KR": "KR",
}


def task_catalog() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for platform, tasks in PLATFORM_TASKS.items():
        groups.append({"platform": platform, "tasks": tasks})
    return groups


def find_task(task_id: str, platform: str | None = None) -> dict[str, str] | None:
    keys = [platform] if platform else []
    keys.extend([key for key in PLATFORM_TASKS if key not in keys])
    for key in keys:
        for task in PLATFORM_TASKS.get(key or "", []):
            if task["id"] == task_id:
                return task
    return None


def infer_commerce(profile: dict[str, Any]) -> dict[str, Any]:
    tags = [str(tag).lower() for tag in profile.get("tags") or []]
    name = str(profile.get("name") or "").lower()
    joined = " ".join([name, *tags])

    platform = "tiktok_shop" if "tiktok" in joined else "social"
    if "amazon" in joined:
        platform = "amazon"
    elif "shopify" in joined:
        platform = "shopify"
    elif "etsy" in joined:
        platform = "etsy"
    elif "ebay" in joined:
        platform = "ebay"

    brand = next((tag for tag in tags if tag not in {"tiktok", "shop", "amazon", "shopify", "etsy", "ebay"}), "")
    if not brand:
        brand = name.split("-")[0] if name else "unassigned"

    return {
        "platform": platform,
        "brand": brand,
        "market": "US",
        "owner": "未分配",
        "account_status": "normal",
        "priority": "normal",
        "daily_goal": "",
        "notes": "",
    }


def ensure_commerce(profile: dict[str, Any]) -> dict[str, Any]:
    commerce = {**infer_commerce(profile), **(profile.get("commerce") or {})}
    profile["commerce"] = commerce
    profile["health"] = summarize_health(profile)
    return profile


def summarize_health(profile: dict[str, Any]) -> dict[str, Any]:
    commerce = profile.get("commerce") or infer_commerce(profile)
    proxy = profile.get("proxy") or {}
    fp = profile.get("fingerprint") or {}
    last_check = proxy.get("last_check") or {}
    risks: list[str] = []

    expected_country = MARKET_COUNTRY.get(str(commerce.get("market") or "").upper())
    actual_country = str(last_check.get("country") or "").upper()
    if proxy.get("mode") == "direct":
        risks.append("直连未绑定代理")
    elif expected_country and actual_country and actual_country != expected_country:
        risks.append(f"代理国家 {actual_country} 与市场 {commerce.get('market')} 不一致")
    elif expected_country and not actual_country:
        risks.append("代理未检测")

    timezone_id = str(fp.get("timezone") or "")
    proxy_timezone = str(last_check.get("timezone") or "")
    if commerce.get("market") == "US" and timezone_id and not timezone_id.startswith("America/"):
        risks.append("美国市场但时区不是 America/*")
    if proxy_timezone and timezone_id and proxy_timezone != timezone_id:
        risks.append(f"代理时区 {proxy_timezone} 与指纹时区 {timezone_id} 不一致")

    if profile.get("last_error"):
        risks.append("最近启动有错误")
    if profile.get("status") == "running":
        last_launch = _parse_dt(profile.get("last_launch_at"))
        if last_launch:
            minutes = (datetime.now(timezone.utc) - last_launch).total_seconds() / 60
            if minutes < 5:
                risks.append("刚启动不久")

    score = max(0, 100 - len(risks) * 22)
    level = "good" if score >= 80 else "warn" if score >= 50 else "risk"
    return {
        "score": score,
        "level": level,
        "risks": risks,
        "proxy_country": actual_country,
        "proxy_ip": last_check.get("ip", ""),
        "proxy_timezone": proxy_timezone,
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def migrate_ecommerce_profiles() -> int:
    changed = 0
    for profile in list_profiles():
        before = profile.get("commerce")
        ensure_commerce(profile)
        if before != profile.get("commerce") or "health" not in profile:
            upsert_profile(profile)
            changed += 1
    return changed
