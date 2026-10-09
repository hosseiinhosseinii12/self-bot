"""Repeat jobs: .rep <sec> <min> <text>, .stop. Templates, schedules, variables."""
import asyncio
import random
import string
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional

from .config import CONFIG
from .economy import cost_job, cost_job_message, spend
from .logging_setup import log_jobs
from .store import history_store, templates_store
from .subscriptions import can_create_job, can_use_duration, can_use_interval, plan_limits

MIN_REPEAT_SEC = 60
MAX_DURATION_MIN = 720
MAX_MESSAGES = 200
MAX_TEMPLATES = 20
MAX_HISTORY = 30

# in-memory active jobs: job_id -> task
_active_jobs: Dict[str, asyncio.Task] = {}
_job_meta: Dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _rand_id(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def save_template(user_id: int, name: str, text: str) -> tuple:
    key = str(user_id)
    tpls = templates_store.get(key) or {}
    if not isinstance(tpls, dict):
        tpls = {}
    if name not in tpls and len(tpls) >= MAX_TEMPLATES:
        return False, f"Template limit reached ({MAX_TEMPLATES})."
    tpls[name] = text
    templates_store.set(key, tpls)
    return True, "saved"


def load_template(user_id: int, name: str) -> Optional[str]:
    tpls = templates_store.get(str(user_id)) or {}
    if isinstance(tpls, dict):
        return tpls.get(name)
    return None


def list_templates(user_id: int) -> dict:
    tpls = templates_store.get(str(user_id)) or {}
    return tpls if isinstance(tpls, dict) else {}


def delete_template(user_id: int, name: str) -> bool:
    tpls = templates_store.get(str(user_id)) or {}
    if isinstance(tpls, dict) and name in tpls:
        del tpls[name]
        templates_store.set(str(user_id), tpls)
        return True
    return False


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def _append_history(entry: dict) -> None:
    data = history_store.all()
    if not isinstance(data, list):
        data = []
    data.append(entry)
    if len(data) > MAX_HISTORY:
        data = data[-MAX_HISTORY:]
    history_store.replace(data)


def history() -> List[dict]:
    data = history_store.all()
    if not isinstance(data, list):
        return []
    return list(reversed(data))


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
def render_variables(text: str, job: dict, sent_count: int) -> str:
    now = datetime.now()
    subs = {
        "{time}": now.strftime("%H:%M"),
        "{date}": now.strftime("%Y-%m-%d"),
        "{job_id}": job.get("id", ""),
        "{sent}": str(sent_count),
        "{name}": CONFIG.get("base_name", "User"),
    }
    out = text
    for k, v in subs.items():
        out = out.replace(k, v)
    return out


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------
def _parse_schedule(schedule: Optional[str]) -> tuple:
    """schedule is 'HH:MM-HH:MM' or None. Returns (start, end) or (None, None)."""
    if not schedule:
        return None, None
    if "-" not in schedule:
        return None, None
    a, b = schedule.split("-", 1)
    try:
        sh, sm = map(int, a.split(":"))
        eh, em = map(int, b.split(":"))
        return (sh, sm), (eh, em)
    except Exception:
        return None, None


def _within_schedule(start, end) -> bool:
    if not start or not end:
        return True
    now = datetime.now()
    cur = now.hour * 60 + now.minute
    s = start[0] * 60 + start[1]
    e = end[0] * 60 + end[1]
    if s <= e:
        return s <= cur <= e
    # crosses midnight
    return cur >= s or cur <= e


def create_job(
    owner_id: int,
    chat_id: int,
    interval_seconds: int,
    duration_minutes: int,
    text: str,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
) -> tuple:
    """Validate and register a new job. Returns (ok, job_or_reason)."""
    interval_seconds = max(MIN_REPEAT_SEC, int(interval_seconds))
    duration_minutes = max(1, min(MAX_DURATION_MIN, int(duration_minutes)))

    ok, reason = can_create_job(owner_id, len(_active_jobs))
    if not ok:
        return False, reason
    ok, reason = can_use_interval(owner_id, interval_seconds)
    if not ok:
        return False, reason
    ok, reason = can_use_duration(owner_id, duration_minutes)
    if not ok:
        return False, reason

    if not spend(owner_id, cost_job(), "job_create"):
        return False, "insufficient"

    job_id = _rand_id()
    job = {
        "id": job_id,
        "owner_id": int(owner_id),
        "chat_id": int(chat_id),
        "interval": interval_seconds,
        "duration": duration_minutes,
        "text": text,
        "schedule": (
            f"{start_at}-{end_at}" if start_at and end_at else None
        ),
        "created_at": _now_iso(),
        "sent": 0,
        "status": "running",
    }
    _job_meta[job_id] = job
    log_jobs.info(f"job created id={job_id} owner={owner_id} every={interval_seconds}s for {duration_minutes}m")
    return True, job


def register_task(job_id: str, task: asyncio.Task) -> None:
    _active_jobs[job_id] = task


def active_jobs() -> List[dict]:
    return [j for j in _job_meta.values() if j.get("status") == "running"]


def active_count() -> int:
    return len(active_jobs())


def stop_jobs_in_chat(chat_id: int) -> int:
    n = 0
    for jid, job in list(_job_meta.items()):
        if job.get("chat_id") == int(chat_id) and job.get("status") == "running":
            task = _active_jobs.get(jid)
            if task and not task.done():
                task.cancel()
            job["status"] = "stopped"
            job["finished_at"] = _now_iso()
            _append_history(job)
            _active_jobs.pop(jid, None)
            n += 1
    return n


def stop_all() -> int:
    n = 0
    for jid, task in list(_active_jobs.items()):
        if not task.done():
            task.cancel()
        job = _job_meta.get(jid)
        if job:
            job["status"] = "stopped"
            job["finished_at"] = _now_iso()
            _append_history(job)
        _active_jobs.pop(jid, None)
        n += 1
    return n


# ---------------------------------------------------------------------------
# The actual runner
# ---------------------------------------------------------------------------
async def run_job(bot_client, job: dict) -> None:
    """Send messages at intervals until duration ends or cancelled."""
    start = datetime.now(dt_timezone.utc)
    deadline = start + timedelta(minutes=job["duration"])
    interval = job["interval"]
    start_sched, end_sched = _parse_schedule(job.get("schedule"))

    try:
        while datetime.now(dt_timezone.utc) < deadline:
            if job["sent"] >= MAX_MESSAGES:
                job["status"] = "max_messages"
                break
            if not _within_schedule(start_sched, end_sched):
                await asyncio.sleep(30)
                continue

            rendered = render_variables(job["text"], job, job["sent"] + 1)

            sent_ok = False
            for attempt in range(3):
                try:
                    await bot_client.send_message(int(job["chat_id"]), rendered)
                    sent_ok = True
                    break
                except Exception as e:
                    log_jobs.warning(f"send attempt {attempt+1} failed: {e}")
                    await asyncio.sleep(2)
            if not sent_ok:
                log_jobs.error(f"giving up on send for job={job['id']}")
                job["status"] = "send_failed"
                break

            job["sent"] += 1
            # per-message diamond cost for free/basic users
            owner = job["owner_id"]
            from .subscriptions import effective_plan
            if effective_plan(owner) in ("free", "basic"):
                if not spend(owner, cost_job_message(), "job_message"):
                    job["status"] = "insufficient"
                    break

            await asyncio.sleep(interval)

        else:
            job["status"] = "completed"

    except asyncio.CancelledError:
        job["status"] = "stopped"
        raise
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        log_jobs.exception(f"job {job['id']} crashed: {e}")
    finally:
        job["finished_at"] = _now_iso()
        _append_history(job)
        _active_jobs.pop(job["id"], None)