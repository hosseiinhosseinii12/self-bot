"""Profile-name clock loop with task-id deduplication and graceful shutdown."""
import asyncio
import re
import time
from datetime import datetime
from typing import Optional

from .config import CONFIG
from .economy import cost_clock, spend
from .fonts import apply_clock_font, apply_name_font
from .logging_setup import log_clock
from .store import user_data_store

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# module-level state to prevent duplicate writes across restarts
_last_written_name: Optional[str] = None
_current_task_id: Optional[int] = None
_clock_task: Optional[asyncio.Task] = None

TIME_RE = re.compile(r"^(.+?)\s+\d{1,2}:\d{2}$")


def _owner_id() -> int:
    return int(CONFIG.get("owner_id", 338266658))


def _build_target_name(base_name: str, now: datetime) -> str:
    clock_str = f"{now.hour:02d}:{now.minute:02d}"
    styled_base = apply_name_font(base_name, CONFIG.get("name_font", "normal"))
    styled_clock = apply_clock_font(clock_str, CONFIG.get("clock_font", "double"))
    return f"{styled_base} {styled_clock}"


def _strip_time(name: str) -> str:
    if not name:
        return name
    m = TIME_RE.match(name.strip())
    if m:
        return m.group(1).strip()
    # also strip if it's just styled digits at the end
    return name


async def _write_name(user_client, new_name: str) -> bool:
    """Write profile first name. Return True on success."""
    global _last_written_name
    if new_name == _last_written_name:
        return True
    try:
        from telethon.tl.functions.account import UpdateProfileRequest
        await user_client(UpdateProfileRequest(first_name=new_name))
        _last_written_name = new_name
        return True
    except Exception as e:
        log_clock.error(f"write name failed: {e}")
        return False


async def strip_time_now(user_client) -> bool:
    """Force-strip time from current name using base_name from config."""
    base = CONFIG.get("base_name", "User")
    styled_base = apply_name_font(base, CONFIG.get("name_font", "normal"))
    ok = await _write_name(user_client, styled_base)
    if ok:
        log_clock.info(f"stripped time, set base name to {styled_base!r}")
    return ok


async def write_base_name_sync(user_client) -> bool:
    """Used on startup when previous run ended uncleanly."""
    base = CONFIG.get("base_name", "User")
    styled_base = apply_name_font(base, CONFIG.get("name_font", "normal"))
    return await _write_name(user_client, styled_base)


def _should_write_now(owner_id: int) -> bool:
    """Free/basic users pay diamonds per clock update."""
    from .subscriptions import effective_plan
    plan = effective_plan(owner_id)
    if plan in ("pro", "vip"):
        return True
    cost = cost_clock()
    if cost <= 0:
        return True
    return spend(owner_id, cost, "clock_update")


async def _clock_loop(user_client, task_id: int) -> None:
    global _current_task_id
    _current_task_id = task_id
    log_clock.info(f"clock loop started task_id={task_id}")

    while True:
        if _current_task_id != task_id:
            log_clock.info(f"clock loop task_id={task_id} superseded, exiting")
            return
        if not CONFIG.get("clock_on"):
            log_clock.info(f"clock loop task_id={task_id} disabled, exiting")
            return

        owner_id = _owner_id()

        # ensure diamonds if needed
        if not _should_write_now(owner_id):
            log_clock.warning(
                f"insufficient diamonds for clock update (owner={owner_id}), pausing"
            )
            await asyncio.sleep(30)
            continue

        tz_name = CONFIG.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)

        base = CONFIG.get("base_name", "User")
        target = _build_target_name(base, now)
        await _write_name(user_client, target)

        interval = max(1, min(60, int(CONFIG.get("interval", 5))))
        # sleep in small chunks so we can react to task changes / shutdown
        for _ in range(interval * 60):
            if _current_task_id != task_id or not CONFIG.get("clock_on"):
                return
            await asyncio.sleep(1)


def start_clock(user_client) -> int:
    """Start (or restart) the clock loop. Returns task_id."""
    global _clock_task, _current_task_id
    _current_task_id = int(time.time() * 1000)
    _clock_task = asyncio.create_task(_clock_loop(user_client, _current_task_id))
    return _current_task_id


async def stop_clock(user_client) -> None:
    """Stop the loop and strip time from name."""
    global _clock_task, _current_task_id
    CONFIG["clock_on"] = False
    from .config import save_config
    save_config(CONFIG)
    _current_task_id = None
    if _clock_task and not _clock_task.done():
        _clock_task.cancel()
        try:
            await _clock_task
        except (asyncio.CancelledError, Exception):
            pass
    _clock_task = None
    await strip_time_now(user_client)


async def shutdown_clock(user_client) -> None:
    """Graceful shutdown: write base name reliably."""
    global _current_task_id
    _current_task_id = None
    try:
        await asyncio.wait_for(
            asyncio.shield(strip_time_now(user_client)),
            timeout=8.0,
        )
    except Exception:
        # sync fallback — run a fresh event loop
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(strip_time_now(user_client))
            loop.close()
        except Exception as e:
            log_clock.error(f"sync fallback failed: {e}")


def last_written() -> Optional[str]:
    return _last_written_name


def reset_last_written() -> None:
    global _last_written_name
    _last_written_name = None