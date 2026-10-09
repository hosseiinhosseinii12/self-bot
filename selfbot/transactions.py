"""Read helpers for the transaction ledger."""
from typing import List

from .store import transactions_store


def all_transactions() -> List[dict]:
    data = transactions_store.all()
    return data if isinstance(data, list) else []


def for_user(user_id: int, limit: int = 100) -> List[dict]:
    out = [tx for tx in all_transactions() if int(tx.get("user_id", 0)) == int(user_id)]
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out[:limit]


def recent(limit: int = 200) -> List[dict]:
    out = all_transactions()
    out = sorted(out, key=lambda x: x.get("created_at", ""), reverse=True)
    return out[:limit]


def top_spenders(limit: int = 10) -> List[dict]:
    totals = {}
    for tx in all_transactions():
        if tx.get("kind") != "spend":
            continue
        uid = int(tx.get("user_id", 0))
        totals[uid] = totals.get(uid, 0) + int(tx.get("amount", 0) or 0)
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"user_id": uid, "spent": spent} for uid, spent in ranked]


def totals() -> dict:
    granted = spent = refunded = 0
    for tx in all_transactions():
        amt = int(tx.get("amount", 0) or 0)
        kind = tx.get("kind")
        if kind == "grant":
            granted += amt
        elif kind == "spend":
            spent += amt
        elif kind == "refund":
            refunded += amt
    return {"granted": granted, "spent": spent, "refunded": refunded}