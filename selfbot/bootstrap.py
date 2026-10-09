"""Boot and shutdown orchestration for the whole app."""
import asyncio
import threading
from pathlib import Path

from .config import CONFIG, DB_PATH, get_api_credentials, android_fallback, save_config
from .logging_setup import log, log_bot, log_clock
from .store import users_store
from .users import ensure_user
from .economy import grant
from .subscriptions import set_sub

try:
    from telethon import TelegramClient
    from telethon.errors import ApiIdPublishedFloodError
except ImportError:
    TelegramClient = None  # type: ignore


# ---------------------------------------------------------------------------
# Seeding owner
# ---------------------------------------------------------------------------
def _seed_owner() -> None:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = ensure_user(owner, first_name="Owner")
    if int(u.get("diamonds", 0)) < 999999:
        from .economy import set_balance
        set_balance(owner, 999999)
    # grant VIP 36500 days
    set_sub(owner, "vip", days=36500, auto_renew=True)


# ---------------------------------------------------------------------------
# Telegram clients
# ---------------------------------------------------------------------------
async def _make_bot_client(runtime):
    api_id, api_hash = get_api_credentials()
    bot_token = CONFIG.get("bot_token") or ""
    if not bot_token:
        raise RuntimeError("BOT_TOKEN not set.")

    from .proxy import get_proxy_url, proxy_connector
    proxy_url = get_proxy_url()

    kwargs = {}
    if proxy_url:
        # use custom connector via connection class override
        # Telethon supports proxy= for socks, but we go through our embedded bridge
        kwargs["proxy"] = None  # bridge is transparent on 127.0.0.1:1080

    session_path = str(DB_PATH / "bot.session")
    try:
        client = TelegramClient(session_path, api_id, api_hash, **kwargs)
        await client.start(bot_token=bot_token)
        return client
    except ApiIdPublishedFloodError:
        log_bot.warning("API_ID_PUBLISHED_FLOOD — retrying with Android public creds")
        aid, ahash = android_fallback()
        client = TelegramClient(session_path, aid, ahash, **kwargs)
        await client.start(bot_token=bot_token)
        return client


async def _make_user_client(runtime):
    api_id, api_hash = get_api_credentials()
    session_path = str(DB_PATH / "user.session")
    if not Path(session_path).exists():
        return None
    try:
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            return None
        return client
    except Exception as e:
        log_bot.warning(f"user client init failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
_async_loop = None


def boot_all(runtime) -> None:
    """Boot everything in a dedicated event loop (runs in a background thread)."""
    global _async_loop
    loop = asyncio.new_event_loop()
    _async_loop = loop
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(_boot_async(runtime))
        loop.run_forever()
    except Exception as e:
        log.exception(f"boot failed: {e}")


async def _boot_async(runtime) -> None:
    log.info("=== SELF BOT booting ===")

    # 1) Embedded SOCKS5 bridge
    try:
        from .proxy import SocksToWorkerBridge, test_proxy, get_proxy_url
        if get_proxy_url():
            bridge = SocksToWorkerBridge()
            await bridge.start()
            runtime._bridge = bridge
            # probe (best-effort)
            try:
                res = test_proxy()
                log.info(f"proxy probe: {res}")
            except Exception as e:
                log.warning(f"proxy probe failed: {e}")
    except Exception as e:
        log.warning(f"SOCKS bridge failed to start: {e}")

    # 2) Seed owner
    _seed_owner()

    # 3) Bot client
    try:
        runtime.bot_client = await _make_bot_client(runtime)
        log_bot.info("bot client connected")
    except Exception as e:
        log_bot.error(f"bot client failed: {e}")
        return

    # 4) User client (if session exists)
    runtime.user_client = await _make_user_client(runtime)
    if runtime.user_client:
        log_bot.info("user client connected")
        # strip time on startup if previous run ended uncleanly
        try:
            from .clock import strip_time_now
            await strip_time_now(runtime.user_client)
        except Exception as e:
            log_clock.warning(f"startup strip failed: {e}")
        if CONFIG.get("clock_on"):
            from .clock import start_clock
            start_clock(runtime.user_client)

    # 5) Register handlers
    from .bot_handlers import _register_handlers, start_watchdog
    _register_handlers(runtime)
    await start_watchdog(runtime)

    # 6) Scheduler (backups, auto-renew)
    _start_scheduler(runtime)

    log.info("=== SELF BOT ready ===")


def _start_scheduler(runtime) -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("APScheduler not installed; skipping scheduled tasks")
        return

    sched = BackgroundScheduler(timezone="UTC")

    def _daily_backup():
        try:
            from .backup import create_and_send_backup
            create_and_send_backup(runtime)
        except Exception as e:
            log.exception(f"daily backup failed: {e}")

    def _daily_sub_check():
        try:
            from .subscriptions import process_auto_renew_and_expiry
            process_auto_renew_and_expiry()
        except Exception as e:
            log.exception(f"sub check failed: {e}")

    sched.add_job(_daily_backup, "cron", hour=3, minute=0)
    sched.add_job(_daily_sub_check, "cron", hour=4, minute=0)
    sched.start()
    runtime._scheduler = sched
    log.info("scheduler started (backup @ 3AM, sub check @ 4AM)")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
def shutdown_all(runtime) -> None:
    """Synchronous shutdown from atexit / signal handler."""
    if runtime is None:
        return
    try:
        loop = _async_loop
        if loop and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_shutdown_async(runtime), loop)
            try:
                fut.result(timeout=15)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
    except Exception as e:
        log.error(f"shutdown error: {e}")


async def _shutdown_async(runtime) -> None:
    log.info("shutting down…")
    try:
        from .bot_handlers import stop_watchdog
        await stop_watchdog(runtime)
    except Exception:
        pass
    # strip time from name
    try:
        if runtime.user_client:
            from .clock import shutdown_clock
            await shutdown_clock(runtime.user_client)
    except Exception as e:
        log_clock.warning(f"shutdown strip failed: {e}")
    # stop bridge
    try:
        if getattr(runtime, "_bridge", None):
            await runtime._bridge.stop()
    except Exception:
        pass
    # stop scheduler
    try:
        if getattr(runtime, "_scheduler", None):
            runtime._scheduler.shutdown(wait=False)
    except Exception:
        pass
    # disconnect clients
    try:
        if runtime.user_client:
            await runtime.user_client.disconnect()
    except Exception:
        pass
    try:
        if runtime.bot_client:
            await runtime.bot_client.disconnect()
    except Exception:
        pass
    log.info("shutdown complete")