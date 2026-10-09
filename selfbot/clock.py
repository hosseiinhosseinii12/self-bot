"""Per-user profile-name clock."""
import asyncio
import re
import time
from datetime import datetime
from typing import Dict, Optional

from .config import CONFIG
from .economy import cost_clock, spend
from .fonts import apply_clock_font, apply_name_font
from .logging_setup import log_clock
from .users import get_user_settings, update_user_settings

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

TIME_RE = re.compile(r"^(.+?)\s+\d{1,2}:\d{2}$")

_clock_tasks: Dict[int, dict] = {}


def _build_target_name(settings: dict, now: datetime) -> str:
    clock_str = f"{now.hour:02d}:{now.minute:02d}"
    styled_base = apply_name_font(
        settings.get("base_name", "User"),
        settings.get("name_font", "normal"),
        custom=settings.get("custom_name_font", "") or "",
    )
    styled_clock = apply_clock_font(
        clock_str,
        settings.get("clock_font", "double"),
        custom=settings.get("custom_clock_font", "") or "",
    )
    return f"{styled_base} {styled_clock}"


async def _write_name(user_client, new_name: str, user_id: int) -> bool:
    slot = _clock_tasks.setdefault(
        int(user_id), {"task_id": None, "task": None, "last": None})
    if new_name == slot.get("last"):
        return True
    try:
        from telethon.tl.functions.account import UpdateProfileRequest
        await user_client(UpdateProfileRequest(first_name=new_name))
        slot["last"] = new_name
        return True
    except Exception as e:
        log_clock.error(f"write name failed for user {user_id}: {e}")
        return False


async def strip_time_now(user_client, user_id: int) -> bool:
    settings = get_user_settings(user_id)
    styled_base = apply_name_font(
        settings.get("base_name", "User"),
        settings.get("name_font", "normal"),
        custom=settings.get("custom_name_font", "") or "",
    )
    ok = await _write_name(user_client, styled_base, user_id)
    if ok:
        log_clock.info(f"stripped time for user {user_id} → {styled_base!r}")
    return ok


async def write_base_name_sync(user_client, user_id: int = None) -> bool:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    styled_base = apply_name_font(
        settings.get("base_name", "User"),
        settings.get("name_font", "normal"),
        custom=settings.get("custom_name_font", "") or "",
    )
    return await _write_name(user_client, styled_base, uid)


def _should_write_now(user_id: int) -> bool:
    cost = cost_clock()
    if cost <= 0:
        return True
    return spend(user_id, cost, "clock_update")


async def _clock_loop(user_client, user_id: int, task_id: str) -> None:
    log_clock.info(f"clock loop started user={user_id} task_id={task_id}")
    while True:
        slot = _clock_tasks.get(user_id) or {}
        if slot.get("task_id") != task_id:
            return
        settings = get_user_settings(user_id)
        if not settings.get("clock_on"):
            return
        if not _should_write_now(user_id):
            log_clock.warning(f"insufficient diamonds for user {user_id}, pausing")
            await asyncio.sleep(30)
            continue
        tz_name = settings.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        target = _build_target_name(settings, now)
        await _write_name(user_client, target, user_id)
        interval = max(1, min(60, int(settings.get("interval", 5))))
        for _ in range(interval * 60):
            slot = _clock_tasks.get(user_id) or {}
            if slot.get("task_id") != task_id:
                return
            if not get_user_settings(user_id).get("clock_on"):
                return
            await asyncio.sleep(1)


def start_clock(user_client, user_id: int = None) -> str:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    stop_clock(uid)
    task_id = str(int(time.time() * 1000))
    task = asyncio.create_task(_clock_loop(user_client, uid, task_id))
    _clock_tasks[uid] = {"task_id": task_id, "task": task, "last": None}
    update_user_settings(uid, clock_on=True)
    return task_id


def stop_clock(user_id: int = None) -> None:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    slot = _clock_tasks.pop(uid, None)
    if slot and slot.get("task"):
        t = slot["task"]
        if not t.done():
            t.cancel()
    update_user_settings(uid, clock_on=False)


async def stop_clock_async(user_client, user_id: int = None) -> None:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    stop_clock(uid)
    await strip_time_now(user_client, uid)


async def shutdown_clock(user_client, user_id: int = None) -> None:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    stop_clock(uid)
    try:
        await asyncio.wait_for(
            asyncio.shield(strip_time_now(user_client, uid)), timeout=8.0)
    except Exception:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(strip_time_now(user_client, uid))
            loop.close()
        except Exception as e:
            log_clock.error(f"sync fallback failed: {e}")


def last_written(user_id: int = None) -> Optional[str]:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    slot = _clock_tasks.get(uid) or {}
    return slot.get("last")


def reset_last_written(user_id: int = None) -> None:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    slot = _clock_tasks.setdefault(uid, {"task_id": None, "task": None, "last": None})
    slot["last"] = None