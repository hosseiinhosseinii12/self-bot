"""Repeat jobs — DB-backed, per-user, unlimited duration supported."""
import asyncio
import random
import string
import traceback
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, List, Optional

from .config import CONFIG
from .economy import cost_job, cost_job_message, spend
from .logging_setup import log_jobs
from . import db

MIN_REPEAT_SEC = 60
MAX_MESSAGES = 0  # 0 = unlimited
MAX_HISTORY = 30
MAX_TEMPLATES = 20

_active_tasks: Dict[str, asyncio.Task] = {}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _rand_id(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def save_template(user_id: int, name: str, text: str) -> tuple:
    existing = db.list_templates(user_id)
    if name not in existing and len(existing) >= MAX_TEMPLATES:
        return False, f"Template limit reached ({MAX_TEMPLATES})."
    db.save_template(user_id, name, text)
    return True, "saved"


def load_template(user_id: int, name: str) -> Optional[str]:
    return db.list_templates(user_id).get(name)


def list_templates(user_id: int) -> dict:
    return db.list_templates(user_id)


def delete_template(user_id: int, name: str) -> bool:
    if name in db.list_templates(user_id):
        db.delete_template(user_id, name)
        return True
    return False


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
# Create
# ---------------------------------------------------------------------------
def create_job(owner_id: int, chat_id: int, interval_seconds: int,
               duration_minutes: int, text: str) -> tuple:
    """duration_minutes = 0 means unlimited. Returns (ok, job_or_reason)."""
    try:
        interval_seconds = max(MIN_REPEAT_SEC, int(interval_seconds))
        duration_minutes = int(duration_minutes)
        if duration_minutes < 0:
            duration_minutes = 0
    except Exception as e:
        log_jobs.error(f"create_job: invalid input: {e}")
        return False, f"Invalid input: {e}"

    log_jobs.info(
        f"create_job called owner={owner_id} target={chat_id} "
        f"interval={interval_seconds}s duration={duration_minutes}m"
    )

    if not spend(owner_id, cost_job(), "job_create"):
        log_jobs.warning(f"create_job: insufficient diamonds for {owner_id}")
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
    db.create_job_record(job)
    log_jobs.info(f"job persisted id={job_id}")
    return True, job


def register_task(job_id: str, task: asyncio.Task) -> None:
    _active_tasks[job_id] = task
    log_jobs.info(f"task registered id={job_id}")


def active_jobs(owner_id: Optional[int] = None) -> List[dict]:
    return db.active_jobs_db(owner_id)


def active_count(owner_id: Optional[int] = None) -> int:
    return len(active_jobs(owner_id))


def stop_job(job_id: str) -> bool:
    job = db.get_job(job_id)
    if not job or job.get("status") != "running":
        return False
    task = _active_tasks.pop(job_id, None)
    if task and not task.done():
        task.cancel()
    db.update_job(job_id, status="stopped", finished_at=_now_iso())
    try:
        db.add_history(job_id, job["owner_id"], job["chat_id"],
                       job.get("text", ""), job.get("sent", 0))
    except Exception:
        pass
    log_jobs.info(f"job stopped id={job_id}")
    return True


def stop_all(owner_id: Optional[int] = None) -> int:
    n = 0
    for job in db.active_jobs_db(owner_id):
        if stop_job(job["id"]):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
async def run_job(bot_client, job: dict) -> None:
    job_id = job.get("id", "?")
    owner_id = job.get("owner_id")
    target_id = job.get("chat_id")
    interval = int(job.get("interval", 60))
    duration = int(job.get("duration", 0))

    log_jobs.info(
        f"run_job START id={job_id} owner={owner_id} target={target_id} "
        f"interval={interval}s duration={duration}m"
    )

    deadline = None
    if duration > 0:
        deadline = datetime.now(dt_timezone.utc) + timedelta(minutes=duration)

    sent = int(job.get("sent", 0))

    try:
        while True:
            if deadline is not None and datetime.now(dt_timezone.utc) >= deadline:
                log_jobs.info(f"job {job_id}: reached deadline")
                break

            rendered = render_variables(job["text"], job, sent + 1)
            log_jobs.info(f"job {job_id}: sending (attempt {sent+1})")

            sent_ok = False
            last_err = None
            for attempt in range(3):
                try:
                    await bot_client.send_message(int(target_id), rendered)
                    sent_ok = True
                    break
                except Exception as e:
                    last_err = e
                    log_jobs.warning(
                        f"job {job_id}: attempt {attempt+1} failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    await asyncio.sleep(2)

            if not sent_ok:
                log_jobs.error(f"job {job_id}: giving up: {last_err}")
                db.update_job(job_id, status="send_failed",
                              error=str(last_err), finished_at=_now_iso())
                return

            sent += 1
            db.update_job(job_id, sent=sent)
            log_jobs.info(f"job {job_id}: sent {sent} messages")

            if not spend(owner_id, cost_job_message(), "job_message"):
                log_jobs.warning(f"job {job_id}: insufficient diamonds")
                db.update_job(job_id, status="insufficient",
                              finished_at=_now_iso())
                return

            await asyncio.sleep(interval)

        db.update_job(job_id, status="completed", finished_at=_now_iso())

    except asyncio.CancelledError:
        log_jobs.info(f"job {job_id}: cancelled")
        db.update_job(job_id, status="stopped", finished_at=_now_iso())
        raise
    except Exception as e:
        log_jobs.error(
            f"job {job_id}: crashed: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        db.update_job(job_id, status="error", error=str(e),
                      finished_at=_now_iso())
    finally:
        _active_tasks.pop(job_id, None)
        try:
            db.add_history(job_id, owner_id, target_id, job.get("text", ""), sent)
        except Exception:
            pass
        log_jobs.info(f"run_job END id={job_id} sent={sent}")