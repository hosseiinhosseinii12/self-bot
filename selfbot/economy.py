"""Diamond economy: balances, costs, transactions."""
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from .config import CONFIG, DB_PATH
from .logging_setup import log_econ
from .store import transactions_store, users_store


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------
def get_balance(user_id: int) -> int:
    u = users_store.get(str(user_id)) or {}
    return int(u.get("diamonds", 0))


def set_balance(user_id: int, amount: int) -> None:
    key = str(user_id)
    u = users_store.get(key) or {}
    u["diamonds"] = max(0, int(amount))
    users_store.set(key, u)


# ---------------------------------------------------------------------------
# Transaction log
# ---------------------------------------------------------------------------
def _append_tx(user_id: int, amount: int, reason: str, balance_after: int,
               kind: str = "grant", meta: Optional[dict] = None) -> None:
    tx = {
        "id": f"tx_{int(datetime.now().timestamp() * 1000)}_{user_id}",
        "user_id": int(user_id),
        "amount": int(amount),
        "reason": reason,
        "balance_after": int(balance_after),
        "kind": kind,
        "created_at": _now_iso(),
        "meta": meta or {},
    }
    data = transactions_store.all()
    if not isinstance(data, list):
        data = []
    data.append(tx)
    if len(data) > 50000:
        data = data[-50000:]
    transactions_store.replace(data)
    log_econ.info(f"tx user={user_id} {kind} amount={amount} reason={reason} bal={balance_after}")


def grant(user_id: int, amount: int, reason: str, kind: str = "grant",
          meta: Optional[dict] = None) -> int:
    if amount <= 0:
        return get_balance(user_id)
    new_balance = get_balance(user_id) + int(amount)
    set_balance(user_id, new_balance)
    _append_tx(user_id, amount, reason, new_balance, kind=kind, meta=meta)
    return new_balance


def spend(user_id: int, amount: int, reason: str,
          meta: Optional[dict] = None) -> bool:
    if amount <= 0:
        return True
    current = get_balance(user_id)
    if current < amount:
        return False
    new_balance = current - int(amount)
    set_balance(user_id, new_balance)
    _append_tx(user_id, amount, reason, new_balance, kind="spend", meta=meta)
    return True


def refund(user_id: int, amount: int, reason: str, meta: Optional[dict] = None) -> None:
    if amount <= 0:
        return
    grant(user_id, amount, reason, kind="refund", meta=meta)


def has_balance(user_id: int, amount: int) -> bool:
    return get_balance(user_id) >= amount


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------
def cost_clock() -> int:
    return int(CONFIG.get("diamond_cost_clock", 1))


def cost_job() -> int:
    return int(CONFIG.get("diamond_cost_job", 5))


def cost_job_message() -> int:
    return 1


def referral_bonus() -> int:
    return int(CONFIG.get("referral_bonus", 50))


def signup_bonus() -> int:
    return int(CONFIG.get("signup_bonus", 500))


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def total_in_circulation() -> int:
    total = 0
    data = users_store.all()
    if isinstance(data, dict):
        for u in data.values():
            total += int(u.get("diamonds", 0) or 0)
    return total


def hourly_consumption(hours: int = 24) -> list:
    from datetime import datetime, timedelta, timezone as tz
    now = datetime.now(tz.utc).replace(minute=0, second=0, microsecond=0)
    buckets = {}
    for i in range(hours, -1, -1):
        h = now - timedelta(hours=i)
        buckets[h.isoformat()] = 0
    data = transactions_store.all()
    if isinstance(data, list):
        for tx in data:
            if tx.get("kind") != "spend":
                continue
            try:
                t = datetime.fromisoformat(tx["created_at"]).replace(
                    minute=0, second=0, microsecond=0)
            except Exception:
                continue
            key = t.isoformat()
            if key in buckets:
                buckets[key] += int(tx.get("amount", 0) or 0)
    return [{"hour": k, "spent": v} for k, v in sorted(buckets.items())]