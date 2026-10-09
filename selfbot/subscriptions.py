"""Subscription stub — no tiers. All users have the same basic limits.
The only real cost is diamonds.
"""
from datetime import datetime, timezone as dt_timezone

from .store import users_store

# Everything is unlimited now — only diamonds matter
BASE_LIMITS = {
    "max_jobs": 999,
    "min_interval": 60,       # absolute minimum 1 minute
    "max_duration": 720,      # 12 hours max per job
    "cost_per_update": 0,     # paid via diamond_cost_clock
}


def plan_limits(user_id: int) -> dict:
    return dict(BASE_LIMITS)


def effective_plan(user_id: int) -> str:
    """Kept for backwards compat — always returns 'member'."""
    return "member"


def days_left(user_id: int) -> int:
    return 0


def get_sub(user_id: int) -> dict:
    return {"plan": "member", "expires_at": None}


def set_sub(user_id: int, plan: str, days: int = 30, auto_renew: bool = False) -> dict:
    """No-op — kept for backwards compatibility."""
    return {"plan": "member", "expires_at": None}


def cancel_sub(user_id: int) -> None:
    """No-op."""


def can_create_job(user_id: int, current_job_count: int) -> tuple:
    if current_job_count >= BASE_LIMITS["max_jobs"]:
        return False, f"Job limit reached ({BASE_LIMITS['max_jobs']})."
    return True, ""


def can_use_interval(user_id: int, interval_seconds: int) -> tuple:
    if interval_seconds < BASE_LIMITS["min_interval"]:
        return False, f"Minimum interval is {BASE_LIMITS['min_interval']}s."
    return True, ""


def can_use_duration(user_id: int, duration_minutes: int) -> tuple:
    if duration_minutes > BASE_LIMITS["max_duration"]:
        return False, f"Maximum duration is {BASE_LIMITS['max_duration']} minutes."
    return True, ""


def process_auto_renew_and_expiry() -> list:
    return []


def distribution() -> dict:
    return {"member": len(users_store.all() or {})}


# Alias for backward compatibility with existing code
PLANS = {
    "member": BASE_LIMITS,
}