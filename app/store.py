from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "profiles": [],
        "events": [],
        "settings": {
            "created_at": utc_now(),
        },
    }


def load_state() -> dict[str, Any]:
    with _LOCK:
        path = settings.state_file_abs
        if not path.exists():
            state = default_state()
            save_state(state)
            return state
        return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    with _LOCK:
        path = settings.state_file_abs
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def list_profiles() -> list[dict[str, Any]]:
    return deepcopy(load_state().get("profiles", []))


def get_profile(profile_id: str) -> dict[str, Any] | None:
    for profile in list_profiles():
        if profile.get("id") == profile_id:
            return profile
    return None


def upsert_profile(profile: dict[str, Any]) -> None:
    with _LOCK:
        state = load_state()
        profiles = state.setdefault("profiles", [])
        profile["updated_at"] = utc_now()
        for index, existing in enumerate(profiles):
            if existing.get("id") == profile.get("id"):
                profiles[index] = profile
                save_state(state)
                return
        profile.setdefault("created_at", utc_now())
        profiles.append(profile)
        save_state(state)


def update_profile(profile_id: str, **updates: Any) -> dict[str, Any]:
    with _LOCK:
        state = load_state()
        for profile in state.setdefault("profiles", []):
            if profile.get("id") == profile_id:
                profile.update(updates)
                profile["updated_at"] = utc_now()
                save_state(state)
                return deepcopy(profile)
    raise KeyError(profile_id)


def delete_profile(profile_id: str) -> bool:
    with _LOCK:
        state = load_state()
        profiles = state.setdefault("profiles", [])
        next_profiles = [p for p in profiles if p.get("id") != profile_id]
        changed = len(next_profiles) != len(profiles)
        state["profiles"] = next_profiles
        if changed:
            add_event("profile", profile_id, "deleted", {"profile_id": profile_id}, state=state)
            save_state(state)
        return changed


def add_event(category: str, profile_id: str | None, message: str,
              payload: dict[str, Any] | None = None,
              level: str = "info",
              state: dict[str, Any] | None = None) -> None:
    owns_state = state is None
    if state is None:
        state = load_state()
    events = state.setdefault("events", [])
    events.append({
        "ts": utc_now(),
        "level": level,
        "category": category,
        "profile_id": profile_id,
        "message": message,
        "payload": payload or {},
    })
    del events[:-500]
    if owns_state:
        save_state(state)


def profile_runtime_dir(profile_id: str) -> Path:
    path = settings.data_root_abs / profile_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_browser_dir(profile_id: str) -> Path:
    path = profile_runtime_dir(profile_id) / "browser-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_port_file(profile_id: str) -> Path:
    return profile_runtime_dir(profile_id) / "port.txt"


def profile_runner_file(profile_id: str) -> Path:
    return profile_runtime_dir(profile_id) / "runner.json"
