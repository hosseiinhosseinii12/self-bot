"""Subscription plans, enforcement, expiry, auto-renew."""
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional

from .logging_setup import log_econ
from .store import subscriptions_store, users_store

# plan -> limits
PLANS = {
    "free":  {"max_jobs": 1,  "min_interval": 300, "max_duration": 120, "cost_per_update": 1},
    "basic": {"max_jobs": 3,  "min_interval": 120, "max_duration": 240, "cost_per_update": 1},
    "pro":   {"max_jobs": 10, "min_interval": 60,  "max_duration": 480, "cost_per_update": 0},
    "vip":   {"max_jobs": 50, "min_interval": 30,  "max_duration": 720, "cost_per_update": 0},
}


def _now() -> datetime:
    return datetime.now(dt_timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def get_sub(user_id: int) -> dict:
    sub = subscriptions_store.get(str(user_id))
    if not isinstance(sub, dict):
        return {"plan": "free", "expires_at": None, "auto_renew": False}
    return sub


def set_sub(user_id: int, plan: str, days: int = 30, auto_renew: bool = False) -> dict:
    expires = (_now() + timedelta(days=days)).isoformat()
    sub = {"plan": plan, "expires_at": expires, "auto_renew": bool(auto_renew),
           "started_at": _now_iso()}
    subscriptions_store.set(str(user_id), sub)
    log_econ.info(f"sub user={user_id} plan={plan} days={days} auto_renew={auto_renew}")
    return sub


def cancel_sub(user_id: int) -> None:
    subscriptions_store.delete(str(user_id))


def effective_plan(user_id: int) -> str:
    sub = get_sub(user_id)
    plan = sub.get("plan", "free")
    if plan == "free":
        return "free"
    expires = sub.get("expires_at")
    if not expires:
        return "free"
    try:
        exp = datetime.fromisoformat(expires)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt_timezone.utc)
    except Exception:
        return "free"
    if exp <= _now():
        return "free"
    return plan if plan in PLANS else "free"


def days_left(user_id: int) -> int:
    sub = get_sub(user_id)
    expires = sub.get("expires_at")
    if not expires:
        return 0
    try:
        exp = datetime.fromisoformat(expires)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt_timezone.utc)
    except Exception:
        return 0
    delta = exp - _now()
    return max(0, delta.days)


def plan_limits(user_id: int) -> dict:
    return PLANS.get(effective_plan(user_id), PLANS["free"])


# ---------------------------------------------------------------------------
# Enforcement helpers
# ---------------------------------------------------------------------------
def can_create_job(user_id: int, current_job_count: int) -> tuple:
    """Return (ok, reason)."""
    limits = plan_limits(user_id)
    if current_job_count >= limits["max_jobs"]:
        return False, f"Plan '{effective_plan(user_id)}' allows max {limits['max_jobs']} jobs."
    return True, ""


def can_use_interval(user_id: int, interval_seconds: int) -> tuple:
    limits = plan_limits(user_id)
    if interval_seconds < limits["min_interval"]:
        return False, f"Minimum interval for plan '{effective_plan(user_id)}' is {limits['min_interval']}s."
    return True, ""


def can_use_duration(user_id: int, duration_minutes: int) -> tuple:
    limits = plan_limits(user_id)
    if duration_minutes > limits["max_duration"]:
        return False, f"Maximum duration for plan '{effective_plan(user_id)}' is {limits['max_duration']} min."
    return True, ""


# ---------------------------------------------------------------------------
# Auto-renew / expiry cron
# ---------------------------------------------------------------------------
def process_auto_renew_and_expiry() -> list:
    """Run daily. Returns list of user_ids whose subscription changed."""
    changed = []
    data = subscriptions_store.all()
    if not isinstance(data, dict):
        return changed
    now = _now()
    for uid_str, sub in list(data.items()):
        if not isinstance(sub, dict):
            continue
        expires = sub.get("expires_at")
        if not expires:
            continue
        try:
            exp = datetime.fromisoformat(expires)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=dt_timezone.utc)
        except Exception:
            continue
        if exp > now:
            continue
        if sub.get("auto_renew"):
            new_exp = (now + timedelta(days=30)).isoformat()
            sub["expires_at"] = new_exp
            subscriptions_store.set(uid_str, sub)
            changed.append(int(uid_str))
            log_econ.info(f"auto-renewed sub user={uid_str}")
        else:
            sub["plan"] = "free"
            sub["expires_at"] = None
            subscriptions_store.set(uid_str, sub)
            changed.append(int(uid_str))
            log_econ.info(f"expired sub user={uid_str}")
    return changed


def distribution() -> dict:
    """Return plan -> count for admin pie chart."""
    counts = {"free": 0, "basic": 0, "pro": 0, "vip": 0}
    data = subscriptions_store.all()
    if isinstance(data, dict):
        for uid_str in data:
            plan = effective_plan(int(uid_str))
            counts[plan] = counts.get(plan, 0) + 1
    # also count users with no sub as free
    from .store import users_store
    users = users_store.all()
    if isinstance(users, dict):
        for uid_str in users:
            if uid_str not in (data or {}):
                counts["free"] += 1
    return counts