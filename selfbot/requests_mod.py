"""User requests: diamonds or subscription. Admin approves/rejects."""
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional

from .economy import grant
from .logging_setup import log
from .store import requests_store
from .subscriptions import set_sub


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def all_requests() -> List[dict]:
    data = requests_store.all()
    return data if isinstance(data, list) else []


def save_all(items: List[dict]) -> None:
    requests_store.replace(items)


def create_diamond_request(user_id: int, amount: int, note: str = "") -> dict:
    amount = max(1, min(10000, int(amount)))
    req = {
        "id": f"req_{int(datetime.now().timestamp() * 1000)}_{user_id}",
        "user_id": int(user_id),
        "type": "diamonds",
        "amount": amount,
        "plan": None,
        "note": note or "",
        "status": "pending",
        "created_at": _now_iso(),
        "decided_at": None,
        "decided_by": None,
    }
    items = all_requests()
    items.append(req)
    save_all(items)
    log.info(f"request created user={user_id} type=diamonds amount={amount}")
    return req


def create_subscription_request(user_id: int, plan: str, note: str = "") -> dict:
    if plan not in ("basic", "pro", "vip"):
        plan = "basic"
    req = {
        "id": f"req_{int(datetime.now().timestamp() * 1000)}_{user_id}",
        "user_id": int(user_id),
        "type": "subscription",
        "amount": None,
        "plan": plan,
        "note": note or "",
        "status": "pending",
        "created_at": _now_iso(),
        "decided_at": None,
        "decided_by": None,
    }
    items = all_requests()
    items.append(req)
    save_all(items)
    log.info(f"request created user={user_id} type=subscription plan={plan}")
    return req


def pending() -> List[dict]:
    return [r for r in all_requests() if r.get("status") == "pending"]


def for_user(user_id: int) -> List[dict]:
    out = [r for r in all_requests() if int(r.get("user_id", 0)) == int(user_id)]
    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return out


def get(req_id: str) -> Optional[dict]:
    for r in all_requests():
        if r.get("id") == req_id:
            return r
    return None


def approve(req_id: str, admin_id: int) -> Optional[dict]:
    items = all_requests()
    for r in items:
        if r.get("id") != req_id or r.get("status") != "pending":
            continue
        r["status"] = "approved"
        r["decided_at"] = _now_iso()
        r["decided_by"] = int(admin_id)
        save_all(items)
        # side effects
        if r["type"] == "diamonds":
            grant(int(r["user_id"]), int(r["amount"]), "request_approved", meta={"req": req_id})
        elif r["type"] == "subscription":
            set_sub(int(r["user_id"]), r.get("plan", "basic"), days=30, auto_renew=False)
        log.info(f"request approved id={req_id} by={admin_id}")
        return r
    return None


def reject(req_id: str, admin_id: int) -> Optional[dict]:
    items = all_requests()
    for r in items:
        if r.get("id") != req_id or r.get("status") != "pending":
            continue
        r["status"] = "rejected"
        r["decided_at"] = _now_iso()
        r["decided_by"] = int(admin_id)
        save_all(items)
        log.info(f"request rejected id={req_id} by={admin_id}")
        return r
    return None


def approval_rate_series(days: int = 14) -> List[dict]:
    """Return per-day {date, approved, rejected} for the last N days."""
    from datetime import timedelta

    today = datetime.now(dt_timezone.utc).date()
    buckets = {}
    for i in range(days, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        buckets[d] = {"date": d, "approved": 0, "rejected": 0}
    for r in all_requests():
        if r.get("status") not in ("approved", "rejected"):
            continue
        try:
            d = datetime.fromisoformat(r["decided_at"]).date().isoformat()
        except Exception:
            continue
        if d in buckets:
            buckets[d][r["status"]] += 1
    return list(buckets.values())