"""User records: creation, ban, delete, referral codes, notifications,
and per-user settings (clock, fonts, jobs, etc.)."""
import random
import string
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from .economy import get_balance, grant, set_balance, signup_bonus
from .logging_setup import log
from .store import users_store

DEFAULT_NOTIFY = {
    "job_completion": True,
    "diamond_changes": True,
    "subscription_expiry": True,
}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _gen_referral_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


# ===========================================================================
# Core user records
# ===========================================================================
def get_user(user_id: int) -> Optional[dict]:
    return users_store.get(str(user_id))


def ensure_user(user_id: int, username: str = "", first_name: str = "",
                referred_by: Optional[int] = None) -> dict:
    """Create user if missing, refresh soft fields otherwise.
    New users get 500 diamonds by default (SIGNUP_BONUS env overrides).
    """
    key = str(user_id)
    existing = users_store.get(key)
    if existing:
        existing["username"] = username or existing.get("username", "")
        existing["first_name"] = first_name or existing.get("first_name", "")
        existing["last_seen"] = _now_iso()
        users_store.set(key, existing)
        return existing

    record = {
        "id": int(user_id),
        "username": username or "",
        "first_name": first_name or "",
        "diamonds": 0,
        "plan": "member",
        "banned": False,
        "referral_code": _gen_referral_code(),
        "referred_by": int(referred_by) if referred_by else None,
        "referrals": [],
        "notify": dict(DEFAULT_NOTIFY),
        "priority_support": False,
        "unlocked_fonts": [],
        "extra_job_slots": 0,
        "interval_discount": 0,
        "duration_extension": 0,
        "created_at": _now_iso(),
        "last_seen": _now_iso(),
        # per-user settings
        "clock_on": False,
        "interval": 5,
        "timezone": "UTC",
        "base_name": "User",
        "name_font": "normal",
        "clock_font": "double",
        "custom_name_font": "",
        "custom_clock_font": "",
        "job_target": None,
    }
    users_store.set(key, record)
    grant(user_id, signup_bonus(), "signup_bonus")   # 500 by default
    log.info(f"created user={user_id} with {signup_bonus()} diamonds")
    return record


def is_banned(user_id: int) -> bool:
    u = get_user(user_id) or {}
    return bool(u.get("banned", False))


def ban(user_id: int) -> None:
    u = get_user(user_id) or {"id": int(user_id)}
    u["banned"] = True
    users_store.set(str(user_id), u)


def unban(user_id: int) -> None:
    u = get_user(user_id) or {"id": int(user_id)}
    u["banned"] = False
    users_store.set(str(user_id), u)


def delete_user(user_id: int) -> None:
    users_store.delete(str(user_id))


def all_users() -> dict:
    data = users_store.all()
    return data if isinstance(data, dict) else {}


def count() -> int:
    return len(all_users())


def active_last_24h() -> int:
    cutoff = datetime.now(dt_timezone.utc).timestamp() - 86400
    n = 0
    for u in all_users().values():
        try:
            ts = datetime.fromisoformat(u.get("last_seen", "")).timestamp()
            if ts >= cutoff:
                n += 1
        except Exception:
            continue
    return n


def new_last_7d() -> int:
    cutoff = datetime.now(dt_timezone.utc).timestamp() - 7 * 86400
    n = 0
    for u in all_users().values():
        try:
            ts = datetime.fromisoformat(u.get("created_at", "")).timestamp()
            if ts >= cutoff:
                n += 1
        except Exception:
            continue
    return n


# ===========================================================================
# Notifications
# ===========================================================================
def set_notify(user_id: int, key: str, value: bool) -> None:
    u = get_user(user_id) or {"id": int(user_id)}
    notify = u.get("notify") or dict(DEFAULT_NOTIFY)
    if key in notify:
        notify[key] = bool(value)
    u["notify"] = notify
    users_store.set(str(user_id), u)


def get_notify(user_id: int) -> dict:
    u = get_user(user_id) or {}
    return u.get("notify") or dict(DEFAULT_NOTIFY)


# ===========================================================================
# Referrals
# ===========================================================================
def find_by_referral_code(code: str) -> Optional[int]:
    code = code.strip().upper()
    for uid_str, u in all_users().items():
        if str(u.get("referral_code", "")).upper() == code:
            return int(uid_str)
    return None


def add_referral(referrer_id: int, referee_id: int) -> None:
    u = get_user(referrer_id) or {"id": int(referrer_id)}
    refs = u.get("referrals") or []
    if int(referee_id) not in [int(x) for x in refs]:
        refs.append(int(referee_id))
    u["referrals"] = refs
    users_store.set(str(referrer_id), u)


def touch(user_id: int) -> None:
    u = get_user(user_id)
    if u:
        u["last_seen"] = _now_iso()
        users_store.set(str(user_id), u)


# ===========================================================================
# Per-user settings (used by clock, panels, jobs)
# ===========================================================================
def get_setting(user_id: int, key: str, default=None):
    u = get_user(user_id) or {}
    return u.get(key, default)


def set_setting(user_id: int, key: str, value) -> None:
    u = get_user(user_id) or {"id": int(user_id)}
    u[key] = value
    users_store.set(str(user_id), u)


def get_user_settings(user_id: int) -> dict:
    """Return the full settings block for a user, with safe defaults."""
    u = get_user(user_id) or {}
    return {
        "clock_on": bool(u.get("clock_on", False)),
        "interval": int(u.get("interval", 5)),
        "timezone": u.get("timezone", "UTC"),
        "base_name": u.get("base_name", "User"),
        "name_font": u.get("name_font", "normal"),
        "clock_font": u.get("clock_font", "double"),
        "custom_name_font": u.get("custom_name_font", ""),
        "custom_clock_font": u.get("custom_clock_font", ""),
        "job_target": u.get("job_target"),
    }


def update_user_settings(user_id: int, **fields) -> None:
    u = get_user(user_id) or {"id": int(user_id)}
    u.update(fields)
    users_store.set(str(user_id), u)