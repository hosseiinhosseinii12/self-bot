"""One-time migration from JSON stores to SQLite."""
import json
from pathlib import Path

from .config import DB_PATH
from .logging_setup import log
from . import db


def _load_json(name: str, default):
    p = DB_PATH / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def migrate_if_needed() -> None:
    """Migrate once from JSON to SQLite. Creates a marker file."""
    marker = DB_PATH / ".migrated_to_sqlite"
    if marker.exists():
        return
    log.info("migrating JSON stores to SQLite…")

    users = _load_json("users.json", {})
    if isinstance(users, dict):
        for uid, u in users.items():
            if not isinstance(u, dict):
                continue
            try:
                uid_int = int(uid)
            except Exception:
                continue
            db.upsert_user(
                uid_int,
                username=u.get("username", ""),
                first_name=u.get("first_name", ""),
                diamonds=int(u.get("diamonds", 0) or 0),
                plan=u.get("plan", "member"),
                banned=1 if u.get("banned") else 0,
                referral_code=u.get("referral_code", ""),
                referred_by=u.get("referred_by"),
                referrals=json.dumps(u.get("referrals", [])),
                notify=json.dumps(u.get("notify", {})),
                created_at=u.get("created_at", ""),
                last_seen=u.get("last_seen", ""),
                clock_on=1 if u.get("clock_on") else 0,
                interval=int(u.get("interval", 5) or 5),
                timezone=u.get("timezone", "Asia/Tehran"),
                base_name=u.get("base_name", "User"),
                name_font=u.get("name_font", "normal"),
                clock_font=u.get("clock_font", "double"),
                custom_name_font=u.get("custom_name_font", ""),
                custom_clock_font=u.get("custom_clock_font", ""),
                job_target=u.get("job_target"),
                web_token=u.get("web_token", ""),
            )

    txs = _load_json("transactions.json", [])
    if isinstance(txs, list):
        for tx in txs[-50000:]:
            if isinstance(tx, dict):
                db.add_tx(tx)

    reqs = _load_json("requests.json", [])
    if isinstance(reqs, list):
        for r in reqs:
            if isinstance(r, dict):
                db.add_request(r)

    audit = _load_json("audit.json", [])
    if isinstance(audit, list):
        for a in audit:
            if isinstance(a, dict):
                db.add_audit(a)

    try:
        marker.write_text("ok", encoding="utf-8")
    except Exception:
        pass
    log.info("migration complete")