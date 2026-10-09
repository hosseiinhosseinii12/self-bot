"""Boot and shutdown orchestration."""
import asyncio
import threading
from pathlib import Path
from urllib.parse import urlparse

from .config import CONFIG, DB_PATH, android_fallback, get_api_credentials, save_config
from .logging_setup import log, log_bot, log_clock
from .store import users_store
from .users import ensure_user

try:
    from telethon import TelegramClient
    from telethon.errors import ApiIdPublishedFloodError
except ImportError:
    TelegramClient = None  # type: ignore


def _build_telethon_proxy():
    if not CONFIG.get("use_proxy"):
        return None
    proxy_url = CONFIG.get("proxy_url") or ""
    if not proxy_url:
        return None
    try:
        import socks  # PySocks
    except ImportError:
        log_bot.warning("PySocks not installed — proceeding without proxy")
        return None

    try:
        u = urlparse(proxy_url)
        scheme = (u.scheme or "socks5").lower()
        host = u.hostname or "127.0.0.1"
        port = int(u.port or 1080)
    except Exception as e:
        log_bot.warning(f"could not parse proxy URL {proxy_url!r}: {e}")
        return None

    if scheme == "socks5":
        return (socks.SOCKS5, host, port)
    if scheme == "socks4":
        return (socks.SOCKS4, host, port)
    if scheme in ("http", "https"):
        return (socks.HTTP, host, port)
    log_bot.warning(f"unknown proxy scheme {scheme!r}")
    return None


def _seed_owner() -> None:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = ensure_user(owner, first_name="Owner")
    if int(u.get("diamonds", 0)) < 999999:
        from .economy import set_balance
        set_balance(owner, 999999)


async def _set_bot_commands(bot) -> None:
    try:
        from telethon.tl.functions.bots import SetBotCommandsRequest
    except ImportError as e:
        log_bot.warning(f"SetBotCommandsRequest not available: {e}")
        return

    try:
        from telethon.tl.types import BotCommand
    except ImportError:
        try:
            from telethon.tl.types.bots import BotCommand  # type: ignore
        except ImportError as e:
            log_bot.warning(f"BotCommand not available: {e}")
            return

    BotCommandScopeDefault = None
    try:
        from telethon.tl.types import BotCommandScopeDefault  # type: ignore
    except ImportError:
        try:
            from telethon.tl.types.bots import BotCommandScopeDefault  # type: ignore
        except ImportError:
            pass

    commands = [
        BotCommand(command="start",  description="Open the panel"),
        BotCommand(command="help",   description="Show help"),
        BotCommand(command="login",  description="Log in with phone → code"),
        BotCommand(command="logout", description="Delete session"),
    ]

    scopes = []
    if BotCommandScopeDefault:
        try:
            scopes.append(BotCommandScopeDefault())
        except Exception:
            pass

    if not scopes:
        log_bot.warning("no BotCommandScope — skipping command registration")
        return

    for scope in scopes:
        try:
            await bot(SetBotCommandsRequest(
                scope=scope, lang_code="", commands=commands,
            ))
        except Exception as e:
            log_bot.warning(f"could not set commands: {e}")

    log_bot.info(f"registered {len(commands)} commands: start, help, login, logout")


async def _make_bot_client(runtime):
    api_id, api_hash = get_api_credentials()
    bot_token = CONFIG.get("bot_token") or ""
    if not bot_token:
        raise RuntimeError("BOT_TOKEN not set.")

    session_path = str(DB_PATH / "bot.session")
    proxy_tuple = _build_telethon_proxy()
    if proxy_tuple:
        log_bot.info(f"Telethon using proxy {proxy_tuple}")

    try:
        client = TelegramClient(session_path, api_id, api_hash, proxy=proxy_tuple)
        await client.start(bot_token=bot_token)
        return client
    except ApiIdPublishedFloodError:
        log_bot.warning("API_ID_PUBLISHED_FLOOD — retrying with Android creds")
        aid, ahash = android_fallback()
        client = TelegramClient(session_path, aid, ahash, proxy=proxy_tuple)
        await client.start(bot_token=bot_token)
        return client


async def make_user_client_for_login(user_id: int = None):
    api_id, api_hash = get_api_credentials()
    if user_id is not None:
        session_path = str(DB_PATH / f"user_{int(user_id)}.session")
    else:
        session_path = str(DB_PATH / "user.session")
    proxy_tuple = _build_telethon_proxy()
    log_bot.info(f"make_user_client_for_login: user={user_id} proxy={proxy_tuple}")

    client = TelegramClient(
        session_path, api_id, api_hash, proxy=proxy_tuple,
        device_model="Pixel 5",
        system_version="11",
        app_version="8.4.1",
        lang_code="en",
        system_lang_code="en-US",
    )
    await client.connect()
    log_bot.info("make_user_client_for_login: connected")
    return client


async def _load_user_sessions(runtime) -> None:
    for uid_str in list((users_store.all() or {}).keys()):
        if not str(uid_str).isdigit():
            continue
        uid = int(uid_str)
        session_file = DB_PATH / f"user_{uid}.session"
        if not session_file.exists():
            continue
        try:
            client = await make_user_client_for_login(uid)
            if await client.is_user_authorized():
                runtime.user_clients[uid] = client
                log_bot.info(f"loaded user session uid={uid}")
                u = users_store.get(str(uid)) or {}
                if u.get("clock_on"):
                    try:
                        from .clock import start_clock
                        start_clock(client, uid)
                    except Exception as e:
                        log_clock.warning(f"clock autostart failed uid={uid}: {e}")
            else:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        except Exception as e:
            log_bot.warning(f"session load failed uid={uid}: {e}")


_async_loop = None


def boot_all(runtime) -> None:
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

    try:
        proxy_tuple = _build_telethon_proxy()
        if proxy_tuple:
            _, host, port = proxy_tuple
            try:
                r, w = await asyncio.wait_for(
                    asyncio.open_connection(host=host, port=port), timeout=5.0)
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass
                log.info(f"Xray SOCKS5 proxy reachable at {host}:{port}")
            except Exception as e:
                log.warning(f"Xray SOCKS5 proxy NOT reachable: {e}")
        else:
            log.info("proxy disabled — connecting directly")
    except Exception as e:
        log.warning(f"proxy check failed: {e}")

    log.info("seeding owner…")
    _seed_owner()
    log.info("owner seeded")

    log.info("connecting bot client…")
    try:
        runtime.bot_client = await _make_bot_client(runtime)
        log_bot.info("bot client connected")
    except Exception as e:
        log_bot.error(f"bot client failed: {e}")
        return

    try:
        await _set_bot_commands(runtime.bot_client)
    except Exception as e:
        log_bot.warning(f"could not set bot commands: {e}")

    code = getattr(runtime, "pairing_code", "------")
    log.info("╔════════════════════════════════════════════╗")
    log.info(f"║  PAIRING CODE:  {code}                    ║")
    log.info("╚════════════════════════════════════════════╝")

    log.info("loading user sessions…")
    await _load_user_sessions(runtime)

    log.info("registering bot handlers…")
    from .bot_handlers import _register_handlers, start_watchdog
    _register_handlers(runtime)
    await start_watchdog(runtime)

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

    sched.add_job(_daily_backup, "cron", hour=3, minute=0)
    sched.start()
    runtime._scheduler = sched
    log.info("scheduler started (backup @ 3AM)")


def shutdown_all(runtime) -> None:
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
    for uid, client in list(getattr(runtime, "user_clients", {}).items()):
        try:
            from .clock import shutdown_clock
            await shutdown_clock(client, uid)
        except Exception as e:
            log_clock.warning(f"shutdown strip failed for uid={uid}: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
    try:
        if getattr(runtime, "_scheduler", None):
            runtime._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        if runtime.bot_client:
            await runtime.bot_client.disconnect()
    except Exception:
        pass
    log.info("shutdown complete")