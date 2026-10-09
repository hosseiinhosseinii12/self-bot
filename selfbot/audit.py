"""Admin audit log: every admin action recorded with IP + timestamp."""
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional

from .store import audit_store


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def record(
    admin_id: int,
    action: str,
    target: Optional[str] = None,
    ip: str = "",
    meta: Optional[dict] = None,
) -> None:
    entry = {
        "id": f"aud_{int(datetime.now().timestamp() * 1000)}",
        "admin_id": int(admin_id),
        "action": action,
        "target": target,
        "ip": ip,
        "created_at": _now_iso(),
        "meta": meta or {},
    }
    data = audit_store.all()
    if not isinstance(data, list):
        data = []
    data.append(entry)
    if len(data) > 20000:
        data = data[-20000:]
    audit_store.replace(data)


def all_entries(limit: int = 500) -> List[dict]:
    data = audit_store.all()
    if not isinstance(data, list):
        return []
    out = sorted(data, key=lambda x: x.get("created_at", ""), reverse=True)
    return out[:limit]


def for_admin(admin_id: int, limit: int = 200) -> List[dict]:
    return [e for e in all_entries(10000) if int(e.get("admin_id", 0)) == int(admin_id)][:limit]