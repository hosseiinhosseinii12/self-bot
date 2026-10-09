"""Diamond economy — DB-backed, atomic."""
from datetime import datetime, timezone as dt_timezone

from .config import CONFIG
from .logging_setup import log_econ
from . import db


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def get_balance(user_id: int) -> int:
    u = db.get_user(user_id)
    return int(u.get("diamonds", 0) or 0) if u else 0


def set_balance(user_id: int, amount: int) -> None:
    db.upsert_user(user_id, diamonds=max(0, int(amount)))


def _append_tx(user_id: int, amount: int, reason: str, balance_after: int,
               kind: str = "grant", meta=None) -> None:
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
    db.add_tx(tx)
    log_econ.info(
        f"tx user={user_id} {kind} amount={amount} reason={reason} bal={balance_after}"
    )


def grant(user_id: int, amount: int, reason: str, kind: str = "grant",
          meta=None) -> int:
    if amount <= 0:
        return get_balance(user_id)
    new_balance = get_balance(user_id) + int(amount)
    set_balance(user_id, new_balance)
    _append_tx(user_id, amount, reason, new_balance, kind=kind, meta=meta)
    return new_balance


def spend(user_id: int, amount: int, reason: str, meta=None) -> bool:
    if amount <= 0:
        return True
    current = get_balance(user_id)
    if current < amount:
        return False
    new_balance = current - int(amount)
    set_balance(user_id, new_balance)
    _append_tx(user_id, amount, reason, new_balance, kind="spend", meta=meta)
    return True


def refund(user_id: int, amount: int, reason: str, meta=None) -> None:
    if amount <= 0:
        return
    grant(user_id, amount, reason, kind="refund", meta=meta)


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


def total_in_circulation() -> int:
    conn = db.get_conn()
    row = conn.execute("SELECT SUM(diamonds) AS s FROM users").fetchone()
    return int(row["s"] or 0) if row else 0


def hourly_consumption(hours: int = 24):
    return db.hourly_spend(hours)