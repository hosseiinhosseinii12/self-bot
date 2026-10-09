"""User records — DB-backed."""
import random
import string
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from .economy import grant, signup_bonus
from .logging_setup import log
from . import db

DEFAULT_NOTIFY = {
    "job_completion": True,
    "diamond_changes": True,
    "subscription_expiry": True,
}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _gen_referral_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def get_user(user_id: int) -> Optional[dict]:
    u = db.get_user(user_id)
    if not u:
        return None
    # enrich parsed fields
    try:
        import json
        u["referrals"] = json.loads(u.get("referrals") or "[]")
    except Exception:
        u["referrals"] = []
    try:
        import json
        u["notify"] = json.loads(u.get("notify") or "{}")
    except Exception:
        u["notify"] = {}
    return u


def ensure_user(user_id: int, username: str = "", first_name: str = "",
                referred_by: Optional[int] = None) -> dict:
    existing = get_user(user_id)
    if existing:
        db.upsert_user(user_id,
                       username=username or existing.get("username", ""),
                       first_name=first_name or existing.get("first_name", ""),
                       last_seen=_now_iso())
        return get_user(user_id)

    import json
    db.upsert_user(
        user_id,
        username=username or "",
        first_name=first_name or "",
        diamonds=0,
        plan="member",
        banned=0,
        referral_code=_gen_referral_code(),
        referred_by=int(referred_by) if referred_by else None,
        referrals=json.dumps([]),
        notify=json.dumps(DEFAULT_NOTIFY),
        created_at=_now_iso(),
        last_seen=_now_iso(),
        clock_on=0,
        interval=5,
        timezone="Asia/Tehran",
        base_name="User",
        name_font="normal",
        clock_font="double",
        custom_name_font="",
        custom_clock_font="",
        job_target=None,
        web_token="",
    )
    grant(user_id, signup_bonus(), "signup_bonus")
    log.info(f"created user={user_id} with {signup_bonus()} diamonds")
    return get_user(user_id)


def is_banned(user_id: int) -> bool:
    u = db.get_user(user_id)
    return bool(u and u.get("banned"))


def ban(user_id: int) -> None:
    db.upsert_user(user_id, banned=1)


def unban(user_id: int) -> None:
    db.upsert_user(user_id, banned=0)


def delete_user(user_id: int) -> None:
    conn = db.get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))


def all_users() -> dict:
    return db.all_users()


def count() -> int:
    return db.count_users()


def active_last_24h() -> int:
    cutoff = datetime.now(dt_timezone.utc).timestamp() - 86400
    n = 0
    for u in db.all_users().values():
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
    for u in db.all_users().values():
        try:
            ts = datetime.fromisoformat(u.get("created_at", "")).timestamp()
            if ts >= cutoff:
                n += 1
        except Exception:
            continue
    return n


def set_notify(user_id: int, key: str, value: bool) -> None:
    import json
    u = get_user(user_id) or {}
    notify = u.get("notify") or dict(DEFAULT_NOTIFY)
    if key in notify:
        notify[key] = bool(value)
    db.upsert_user(user_id, notify=json.dumps(notify))


def get_notify(user_id: int) -> dict:
    u = get_user(user_id) or {}
    return u.get("notify") or dict(DEFAULT_NOTIFY)


def find_by_referral_code(code: str) -> Optional[int]:
    code = code.strip().upper()
    for uid_str, u in db.all_users().items():
        if str(u.get("referral_code", "")).upper() == code:
            return int(uid_str)
    return None


def add_referral(referrer_id: int, referee_id: int) -> None:
    import json
    u = get_user(referrer_id) or {}
    refs = u.get("referrals") or []
    if int(referee_id) not in [int(x) for x in refs]:
        refs.append(int(referee_id))
    db.upsert_user(referrer_id, referrals=json.dumps(refs))


def touch(user_id: int) -> None:
    db.upsert_user(user_id, last_seen=_now_iso())


def get_setting(user_id: int, key: str, default=None):
    u = db.get_user(user_id) or {}
    return u.get(key, default)


def set_setting(user_id: int, key: str, value) -> None:
    db.upsert_user(user_id, **{key: value})


def get_user_settings(user_id: int) -> dict:
    u = db.get_user(user_id) or {}
    return {
        "clock_on": bool(u.get("clock_on", 0)),
        "interval": int(u.get("interval", 5) or 5),
        "timezone": u.get("timezone", "Asia/Tehran"),
        "base_name": u.get("base_name", "User"),
        "name_font": u.get("name_font", "normal"),
        "clock_font": u.get("clock_font", "double"),
        "custom_name_font": u.get("custom_name_font", ""),
        "custom_clock_font": u.get("custom_clock_font", ""),
        "job_target": u.get("job_target"),
    }


def update_user_settings(user_id: int, **fields) -> None:
    db.upsert_user(user_id, **fields)