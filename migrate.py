#!/usr/bin/env python3
"""
migrate.py — upgrade data/*.json to the current schema.
Safe to run multiple times (idempotent).
"""
import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data"))).resolve()


def _load(name: str, default):
    p = DB_PATH / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] {name} corrupt: {e}")
        return default


def _save(name: str, data) -> None:
    p = DB_PATH / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, p)
    print(f"[+] {name} updated")


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


# ---------------------------------------------------------------------------
def migrate_users() -> None:
    users = _load("users.json", {})
    if not isinstance(users, dict):
        return
    changed = False
    for uid, u in users.items():
        if not isinstance(u, dict):
            continue
        defaults = {
            "id": int(uid) if str(uid).isdigit() else 0,
            "username": "",
            "first_name": "",
            "diamonds": 0,
            "plan": "free",
            "banned": False,
            "referral_code": "",
            "referred_by": None,
            "referrals": [],
            "notify": {
                "job_completion": True,
                "diamond_changes": True,
                "subscription_expiry": True,
            },
            "priority_support": False,
            "unlocked_fonts": [],
            "extra_job_slots": 0,
            "interval_discount": 0,
            "duration_extension": 0,
            "shop_purchases": {},
            "created_at": _now_iso(),
            "last_seen": _now_iso(),
        }
        for k, v in defaults.items():
            if k not in u:
                u[k] = v
                changed = True
    if changed:
        _save("users.json", users)
    else:
        print("[=] users.json already up to date")


def migrate_subscriptions() -> None:
    subs = _load("subscriptions.json", {})
    if not isinstance(subs, dict):
        return
    changed = False
    for uid, s in subs.items():
        if not isinstance(s, dict):
            subs[uid] = {"plan": "free", "expires_at": None, "auto_renew": False,
                         "started_at": _now_iso()}
            changed = True
            continue
        if "auto_renew" not in s:
            s["auto_renew"] = False
            changed = True
        if "started_at" not in s:
            s["started_at"] = s.get("expires_at") or _now_iso()
            changed = True
    if changed:
        _save("subscriptions.json", subs)
    else:
        print("[=] subscriptions.json already up to date")


def migrate_transactions() -> None:
    tx = _load("transactions.json", [])
    if not isinstance(tx, list):
        return
    changed = False
    for t in tx:
        if not isinstance(t, dict):
            continue
        if "kind" not in t:
            reason = str(t.get("reason", "")).lower()
            amount = t.get("amount", 0) or 0
            t["kind"] = "spend" if ("spend" in reason or amount < 0) else "grant"
            changed = True
        if "balance_after" not in t:
            t["balance_after"] = 0
            changed = True
        if "meta" not in t:
            t["meta"] = {}
            changed = True
        if "id" not in t:
            t["id"] = f"tx_legacy_{abs(hash((t.get('user_id'), t.get('created_at'))))}"
            changed = True
    if changed:
        _save("transactions.json", tx)
    else:
        print("[=] transactions.json already up to date")


def migrate_requests() -> None:
    reqs = _load("requests.json", [])
    if not isinstance(reqs, list):
        return
    changed = False
    for r in reqs:
        if not isinstance(r, dict):
            continue
        if "decided_by" not in r:
            r["decided_by"] = None
            changed = True
        if "decided_at" not in r:
            r["decided_at"] = None
            changed = True
        if "note" not in r:
            r["note"] = ""
            changed = True
    if changed:
        _save("requests.json", reqs)
    else:
        print("[=] requests.json already up to date")


def migrate_shop() -> None:
    shop = _load("shop.json", {})
    if not isinstance(shop, dict) or "items" not in shop:
        # seed defaults
        from selfbot.shop import DEFAULT_ITEMS
        _save("shop.json", {"items": DEFAULT_ITEMS})
        return
    print("[=] shop.json already up to date")


def migrate_templates() -> None:
    tpls = _load("templates.json", {})
    if not isinstance(tpls, dict):
        return
    print("[=] templates.json already up to date")


def main() -> int:
    print(f"DB_PATH = {DB_PATH}")
    if not DB_PATH.exists():
        print("[!] DB_PATH does not exist. Nothing to migrate.")
        return 0
    migrate_users()
    migrate_subscriptions()
    migrate_transactions()
    migrate_requests()
    migrate_shop()
    migrate_templates()
    print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(BASE_DIR))
    sys.exit(main())