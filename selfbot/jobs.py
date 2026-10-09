"""Repeat jobs — per-user, no tiers. With detailed logging."""
import asyncio
import random
import string
import traceback
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional

from .config import CONFIG
from .economy import cost_job, cost_job_message, spend
from .logging_setup import log_jobs
from .store import history_store

MIN_REPEAT_SEC = 60
MAX_DURATION_MIN = 720
MAX_MESSAGES = 200
MAX_HISTORY = 30

_active_jobs: Dict[str, asyncio.Task] = {}
_job_meta: Dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _rand_id(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _append_history(entry: dict) -> None:
    try:
        data = history_store.all()
        if not isinstance(data, list):
            data = []
        data.append(entry)
        if len(data) > MAX_HISTORY:
            data = data[-MAX_HISTORY:]
        history_store.replace(data)
    except Exception as e:
        log_jobs.warning(f"history append failed: {e}")


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


def create_job(owner_id: int, chat_id: int, interval_seconds: int,
               duration_minutes: int, text: str) -> tuple:
    """Validate and register a new job. Returns (ok, job_or_reason)."""
    try:
        interval_seconds = max(MIN_REPEAT_SEC, int(interval_seconds))
        duration_minutes = max(1, min(MAX_DURATION_MIN, int(duration_minutes)))
    except Exception as e:
        log_jobs.error(f"create_job: invalid input types: {e}")
        return False, f"Invalid input: {e}"

    log_jobs.info(
        f"create_job called owner={owner_id} target={chat_id} "
        f"interval={interval_seconds}s duration={duration_minutes}m"
    )

    if not spend(owner_id, cost_job(), "job_create"):
        log_jobs.warning(f"create_job: insufficient diamonds for owner={owner_id}")
        return False, "insufficient"

    job_id = _rand_id()
    job = {
        "id": job_id,
        "owner_id": int(owner_id),
        "chat_id": int(chat_id),
        "interval": interval_seconds,
        "duration": duration_minutes,
        "text": text,
        "created_at": _now_iso(),
        "sent": 0,
        "status": "running",
    }
    _job_meta[job_id] = job
    log_jobs.info(f"job created id={job_id} owner={owner_id} target={chat_id}")
    return True, job


def register_task(job_id: str, task: asyncio.Task) -> None:
    _active_jobs[job_id] = task
    log_jobs.info(f"job task registered id={job_id}")


def active_jobs(owner_id: Optional[int] = None) -> List[dict]:
    items = [j for j in _job_meta.values() if j.get("status") == "running"]
    if owner_id is not None:
        items = [j for j in items if int(j.get("owner_id", 0)) == int(owner_id)]
    return items


def active_count(owner_id: Optional[int] = None) -> int:
    return len(active_jobs(owner_id))


def stop_all(owner_id: Optional[int] = None) -> int:
    n = 0
    for jid, task in list(_active_jobs.items()):
        job = _job_meta.get(jid)
        if owner_id is not None and job and int(job.get("owner_id", 0)) != int(owner_id):
            continue
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass
        if job:
            job["status"] = "stopped"
            job["finished_at"] = _now_iso()
            _append_history(job)
        _active_jobs.pop(jid, None)
        n += 1
    return n


async def run_job(bot_client, job: dict) -> None:
    """The actual sender loop. Wrapped in try/except so it never silently dies."""
    job_id = job.get("id", "?")
    owner_id = job.get("owner_id")
    target_id = job.get("chat_id")
    interval = job.get("interval", 60)
    duration = job.get("duration", 1)

    log_jobs.info(
        f"run_job START id={job_id} owner={owner_id} "
        f"target={target_id} interval={interval}s duration={duration}m"
    )

    start = datetime.now(dt_timezone.utc)
    deadline = start + timedelta(minutes=duration)

    try:
        while datetime.now(dt_timezone.utc) < deadline:
            if job["sent"] >= MAX_MESSAGES:
                job["status"] = "max_messages"
                log_jobs.info(f"job {job_id}: reached MAX_MESSAGES")
                break

            rendered = render_variables(job["text"], job, job["sent"] + 1)
            log_jobs.info(f"job {job_id}: sending to {target_id} (attempt {job['sent']+1})")

            sent_ok = False
            last_err = None
            for attempt in range(3):
                try:
                    await bot_client.send_message(int(target_id), rendered)
                    sent_ok = True
                    break
                except Exception as e:
                    last_err = e
                    log_jobs.warning(f"job {job_id}: send attempt {attempt+1} failed: {type(e).__name__}: {e}")
                    await asyncio.sleep(2)

            if not sent_ok:
                log_jobs.error(f"job {job_id}: giving up after 3 attempts: {last_err}")
                job["status"] = "send_failed"
                job["error"] = str(last_err)
                break

            job["sent"] += 1
            log_jobs.info(f"job {job_id}: sent {job['sent']} messages so far")

            # Per-message diamond cost
            if not spend(owner_id, cost_job_message(), "job_message"):
                log_jobs.warning(f"job {job_id}: insufficient diamonds for owner {owner_id}")
                job["status"] = "insufficient"
                break

            await asyncio.sleep(interval)
        else:
            job["status"] = "completed"
            log_jobs.info(f"job {job_id}: completed by deadline")

    except asyncio.CancelledError:
        job["status"] = "stopped"
        log_jobs.info(f"job {job_id}: cancelled")
        raise
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        log_jobs.error(f"job {job_id}: crashed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
    finally:
        job["finished_at"] = _now_iso()
        _append_history(job)
        _active_jobs.pop(job_id, None)
        log_jobs.info(f"run_job END id={job_id} status={job.get('status')}")