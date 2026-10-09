"""SQLite storage layer — thread-safe, WAL mode."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from typing import Dict, List, Optional

from .config import DB_PATH

DB_FILE = DB_PATH / "selfbot.db"
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_FILE),
        timeout=30.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    diamonds INTEGER DEFAULT 0,
    plan TEXT DEFAULT 'member',
    banned INTEGER DEFAULT 0,
    referral_code TEXT DEFAULT '',
    referred_by INTEGER,
    referrals TEXT DEFAULT '[]',
    notify TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '',
    last_seen TEXT DEFAULT '',
    clock_on INTEGER DEFAULT 0,
    interval INTEGER DEFAULT 5,
    timezone TEXT DEFAULT 'Asia/Tehran',
    base_name TEXT DEFAULT 'User',
    name_font TEXT DEFAULT 'normal',
    clock_font TEXT DEFAULT 'double',
    custom_name_font TEXT DEFAULT '',
    custom_clock_font TEXT DEFAULT '',
    job_target INTEGER,
    web_token TEXT DEFAULT '',
    data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT,
    balance_after INTEGER,
    kind TEXT,
    created_at TEXT,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_tx_kind ON transactions(kind);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner_id INTEGER,
    chat_id INTEGER,
    interval INTEGER,
    duration INTEGER,
    text TEXT,
    sent INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    created_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    type TEXT,
    amount INTEGER,
    plan TEXT,
    note TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    decided_at TEXT,
    decided_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_req_user ON requests(user_id);
CREATE INDEX IF NOT EXISTS idx_req_status ON requests(status);

CREATE TABLE IF NOT EXISTS audit (
    id TEXT PRIMARY KEY,
    admin_id INTEGER,
    action TEXT,
    target TEXT,
    ip TEXT,
    created_at TEXT,
    meta TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS templates (
    user_id INTEGER,
    name TEXT,
    text TEXT,
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    owner_id INTEGER,
    chat_id INTEGER,
    text TEXT,
    sent INTEGER,
    finished_at TEXT
);
"""


def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    global _initialized
    with _init_lock:
        conn = get_conn()
        conn.executescript(SCHEMA)

        # ensure newer columns exist on 'users'
        cur = conn.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in cur.fetchall()}
        wanted = {
            "clock_on": "INTEGER DEFAULT 0",
            "interval": "INTEGER DEFAULT 5",
            "timezone": "TEXT DEFAULT 'Asia/Tehran'",
            "base_name": "TEXT DEFAULT 'User'",
            "name_font": "TEXT DEFAULT 'normal'",
            "clock_font": "TEXT DEFAULT 'double'",
            "custom_name_font": "TEXT DEFAULT ''",
            "custom_clock_font": "TEXT DEFAULT ''",
            "job_target": "INTEGER",
            "web_token": "TEXT DEFAULT ''",
            "data": "TEXT DEFAULT '{}'",
        }
        for col, decl in wanted.items():
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
                except Exception:
                    pass

        _initialized = True


def _ensure_ready() -> None:
    if not _initialized:
        init_db()


# ===========================================================================
# Users
# ===========================================================================
def get_user(user_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?",
                       (int(user_id),)).fetchone()
    return dict(row) if row else None


def upsert_user(user_id: int, **fields) -> None:
    _ensure_ready()
    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE id = ?",
                             (int(user_id),)).fetchone()

    if existing is None:
        cols = ["id"] + list(fields.keys())
        vals = [int(user_id)] + [
            json.dumps(v) if isinstance(v, (dict, list)) else v
            for v in fields.values()
        ]
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})", vals
        )
    else:
        if not fields:
            return
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        vals.append(int(user_id))
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id = ?", vals)


def all_users() -> Dict[str, dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users").fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def count_users() -> int:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    return int(row["c"]) if row else 0


def delete_user(user_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))


# ===========================================================================
# Transactions
# ===========================================================================
def add_tx(tx: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO transactions "
        "(id, user_id, amount, reason, balance_after, kind, created_at, meta) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            tx.get("id"),
            int(tx.get("user_id", 0)),
            int(tx.get("amount", 0)),
            tx.get("reason", ""),
            int(tx.get("balance_after", 0)),
            tx.get("kind", "grant"),
            tx.get("created_at", ""),
            json.dumps(tx.get("meta", {})),
        ),
    )


def user_transactions(user_id: int, limit: int = 100) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(user_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_transactions(limit: int = 200) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def hourly_spend(hours: int = 24) -> List[dict]:
    _ensure_ready()
    now = datetime.now(dt_timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = {}
    for i in range(hours, -1, -1):
        h = now - __import__("datetime").timedelta(hours=i)
        buckets[h.isoformat()] = 0
    conn = get_conn()
    cutoff = (now - __import__("datetime").timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT created_at, amount FROM transactions "
        "WHERE kind='spend' AND created_at >= ? ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    for r in rows:
        try:
            t = datetime.fromisoformat(r["created_at"]).replace(
                minute=0, second=0, microsecond=0)
        except Exception:
            continue
        key = t.isoformat()
        if key in buckets:
            buckets[key] += int(r["amount"] or 0)
    return [{"hour": k, "spent": v} for k, v in sorted(buckets.items())]


# ===========================================================================
# Jobs
# ===========================================================================
def create_job_record(job: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO jobs "
        "(id, owner_id, chat_id, interval, duration, text, sent, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            job["id"],
            int(job["owner_id"]),
            int(job["chat_id"]),
            int(job["interval"]),
            int(job["duration"]),
            job["text"],
            0,
            "running",
            job.get("created_at", datetime.now(dt_timezone.utc).isoformat()),
        ),
    )


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id = ?", vals)


def get_job(job_id: str) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def active_jobs_db(owner_id: Optional[int] = None) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if owner_id is not None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND owner_id = ? "
            "ORDER BY created_at DESC",
            (int(owner_id),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='running' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def history_recent(owner_id: Optional[int] = None, limit: int = 30) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if owner_id is not None:
        rows = conn.execute(
            "SELECT * FROM history WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
            (int(owner_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def add_history(job_id: str, owner_id: int, chat_id: int, text: str,
                sent: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT INTO history (job_id, owner_id, chat_id, text, sent, finished_at) "
        "VALUES (?,?,?,?,?,?)",
        (job_id, int(owner_id), int(chat_id), (text or "")[:500], int(sent),
         datetime.now(dt_timezone.utc).isoformat()),
    )


# ===========================================================================
# Requests
# ===========================================================================
def add_request(req: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO requests "
        "(id, user_id, type, amount, plan, note, status, created_at, "
        "decided_at, decided_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            req["id"], int(req["user_id"]), req["type"],
            req.get("amount"), req.get("plan"), req.get("note", ""),
            req.get("status", "pending"), req["created_at"],
            req.get("decided_at"), req.get("decided_by"),
        ),
    )


def update_request(req_id: str, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(req_id)
    conn.execute(f"UPDATE requests SET {','.join(sets)} WHERE id = ?", vals)


def list_requests(status: Optional[str] = None,
                  user_id: Optional[int] = None,
                  limit: int = 200) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    q = "SELECT * FROM requests WHERE 1=1"
    vals = []
    if status:
        q += " AND status = ?"
        vals.append(status)
    if user_id is not None:
        q += " AND user_id = ?"
        vals.append(int(user_id))
    q += " ORDER BY created_at DESC LIMIT ?"
    vals.append(int(limit))
    rows = conn.execute(q, vals).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Audit
# ===========================================================================
def add_audit(entry: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO audit "
        "(id, admin_id, action, target, ip, created_at, meta) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            entry["id"], int(entry.get("admin_id", 0)), entry["action"],
            entry.get("target"), entry.get("ip", ""), entry["created_at"],
            json.dumps(entry.get("meta", {})),
        ),
    )


def audit_recent(limit: int = 500) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM audit ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Templates
# ===========================================================================
def save_template(user_id: int, name: str, text: str) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO templates (user_id, name, text) VALUES (?,?,?)",
        (int(user_id), name, text),
    )


def list_templates(user_id: int) -> Dict[str, str]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, text FROM templates WHERE user_id = ?",
        (int(user_id),),
    ).fetchall()
    return {r["name"]: r["text"] for r in rows}


def delete_template(user_id: int, name: str) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM templates WHERE user_id = ? AND name = ?",
                 (int(user_id), name))