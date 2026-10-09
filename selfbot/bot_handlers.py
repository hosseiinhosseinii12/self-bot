"""Telegram bot runtime: bot + user clients, all handlers, watchdog."""
import asyncio
import io
import random
import string
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG, DB_PATH, android_fallback, get_api_credentials, save_config
from .logging_setup import log, log_bot
from .rate_limit import user_limiter
from .store import user_state_store

from . import panels as P
from . import jobs as J
from . import economy as E
from . import requests_mod as R
from . import subscriptions as S
from . import referrals as REF
from . import users as U
from . import rich_msg as RM
from . import clock as CLK
from . import shop as SHOP
from . import qr_login as QRL

try:
    from telethon import Button, TelegramClient, events
    from telethon.errors import (FloodWaitError, MessageNotModifiedError,
                                 QueryIdInvalidError, SessionPasswordNeededError)
    from telethon.sessions import StringSession
except ImportError:
    TelegramClient = None  # type: ignore


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


# ===========================================================================
# Runtime object
# ===========================================================================
class BotRuntime:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_client: Optional[TelegramClient] = None
        self.pairing_code: str = ""
        self.owner_id: int = int(CONFIG.get("owner_id", 338266658))
        self.started: bool = False
        self.stop_event = asyncio.Event()
        self.watchdog_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_targets: dict = {}
        self._awaiting: dict = {}
        self._peer_request_open: bool = False
        # QR login state
        self._qr_client = None
        self._qr_token_hex: Optional[str] = None
        self._qr_expires_at: float = 0.0

    def save_state(self) -> None:
        user_state_store.update({
            "pending_targets": self._pending_targets,
            "awaiting": self._awaiting,
            "peer_request_open": self._peer_request_open,
        })

    def load_state(self) -> None:
        st = user_state_store.all() or {}
        self._pending_targets = st.get("pending_targets") or {}
        self._awaiting = st.get("awaiting") or {}
        self._peer_request_open = bool(st.get("peer_request_open", False))


# ===========================================================================
# Helpers
# ===========================================================================
def _gen_pairing_code() -> str:
    return "".join(random.choices(string.digits, k=6))


def _owner_only(runtime: BotRuntime):
    def deco(fn):
        async def wrapper(event, *a, **kw):
            uid = event.sender_id
            if uid != runtime.owner_id:
                try:
                    await event.answer("Owner only.", alert=True)
                except Exception:
                    pass
                return
            if not user_limiter.allow(uid):
                try:
                    await event.answer("Rate limited.", alert=True)
                except Exception:
                    pass
                return
            return await fn(event, *a, **kw)
        return wrapper
    return deco


def _guarded(runtime: BotRuntime):
    def deco(fn):
        async def wrapper(event, *a, **kw):
            uid = event.sender_id or 0
            if uid and U.is_banned(uid):
                return
            if uid and not user_limiter.allow(uid):
                try:
                    await event.respond("Rate limited.")
                except Exception:
                    pass
                return
            if uid:
                U.touch(uid)
            return await fn(event, *a, **kw)
        return wrapper
    return deco


async def _safe_answer(event, text: str = "", alert: bool = False) -> None:
    try:
        await asyncio.wait_for(event.answer(text, alert=alert), timeout=5.0)
    except (QueryIdInvalidError, asyncio.TimeoutError, Exception):
        pass


# ===========================================================================
# Runtime builder
# ===========================================================================
def build_bot_runtime() -> BotRuntime:
    rt = BotRuntime()
    rt.load_state()
    rt.pairing_code = _gen_pairing_code()
    return rt


# ===========================================================================
# Handler registration
# ===========================================================================
def _register_handlers(rt: BotRuntime) -> None:
    bot = rt.bot_client
    if bot is None:
        return

    # --- /start -----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/start$"))
    @_guarded(rt)
    async def _start(event):
        uid = event.sender_id
        if uid != rt.owner_id:
            await event.respond(
                f"Send the pairing code shown in the server logs to become owner.\n"
                f"({P.t('pairing_prompt')})"
            )
            return
        await event.respond(P.main_panel_text(), buttons=P.main_panel_buttons())

    # --- pairing ----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^\d{6}$"))
    @_guarded(rt)
    async def _pairing(event):
        if event.sender_id == rt.owner_id:
            return
        if event.raw_text.strip() == rt.pairing_code:
            rt.owner_id = int(event.sender_id)
            CONFIG["owner_id"] = rt.owner_id
            save_config(CONFIG)
            U.ensure_user(rt.owner_id, first_name="Owner")
            await event.respond(P.t("pairing_ok"))
            await event.respond(P.main_panel_text(), buttons=P.main_panel_buttons())
        else:
            await event.respond(P.t("pairing_bad"))

    # --- /login -----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/login$"))
    @_owner_only(rt)
    async def _login(event):
        rt._awaiting["phone"] = True
        rt.save_state()
        await event.respond("Send your phone number with country code (e.g. +123456789).")

    # --- /logout ----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/logout$"))
    @_owner_only(rt)
    async def _logout(event):
        try:
            if rt.user_client:
                await rt.user_client.log_out()
        except Exception as e:
            log_bot.warning(f"logout error: {e}")
        session_file = DB_PATH / "user.session"
        if session_file.exists():
            session_file.unlink()
        rt.user_client = None
        await event.respond("Logged out and session deleted.")

    # --- /account ---------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/account$"))
    @_owner_only(rt)
    async def _account(event):
        await event.respond(P.account_text(), buttons=P.account_buttons())

    # --- /request ---------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/request$"))
    @_owner_only(rt)
    async def _request(event):
        await event.respond(P.account_text(), buttons=P.account_buttons())

    # --- /myrequests ------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/myrequests$"))
    @_owner_only(rt)
    async def _myreq(event):
        items = R.for_user(rt.owner_id)[:20]
        if not items:
            await event.respond("No requests yet.")
            return
        lines = ["**My requests**"]
        for r in items:
            lines.append(
                f"• `{r['id']}` — {r['type']} "
                f"{r.get('amount') or r.get('plan')} — {r['status']}"
            )
        await event.respond("\n".join(lines))

    # --- /refer <code> ----------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/refer(?:\s+(\S+))?$"))
    @_guarded(rt)
    async def _refer(event):
        code = event.pattern_match.group(1)
        if not code:
            stats = REF.stats(event.sender_id)
            await event.respond(P.t("your_referral", code=stats["code"]))
            return
        ok, msg = REF.apply_referral(event.sender_id, code)
        if ok:
            await event.respond(P.t("referral_applied", bonus=E.referral_bonus()))
        else:
            await event.respond(P.t(msg))

    # --- /name <base> -----------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/name\s+(.+)$"))
    @_owner_only(rt)
    async def _name(event):
        base = event.pattern_match.group(1).strip()
        CONFIG["base_name"] = base
        save_config(CONFIG)
        if rt.user_client:
            await CLK.write_base_name_sync(rt.user_client)
        await event.respond(f"Base name set to `{base}`.")

    # --- /font <name> -----------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/font\s+(\S+)$"))
    @_owner_only(rt)
    async def _font(event):
        name = event.pattern_match.group(1)
        CONFIG["name_font"] = name
        save_config(CONFIG)
        await event.respond(f"Name font set to `{name}`.")

    # --- /clockfont <name> ------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/clockfont\s+(\S+)$"))
    @_owner_only(rt)
    async def _clockfont(event):
        name = event.pattern_match.group(1)
        CONFIG["clock_font"] = name
        save_config(CONFIG)
        await event.respond(f"Clock font set to `{name}`.")

    # --- /tz <zone> -------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/tz\s+(\S+)$"))
    @_owner_only(rt)
    async def _tz(event):
        zone = event.pattern_match.group(1)
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(zone)
        except Exception:
            await event.respond("Invalid timezone.")
            return
        CONFIG["timezone"] = zone
        save_config(CONFIG)
        await event.respond(f"Timezone set to `{zone}`.")

    # --- /interval <n> ----------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/interval\s+(\d+)$"))
    @_owner_only(rt)
    async def _interval(event):
        n = int(event.pattern_match.group(1))
        if n < 1 or n > 60:
            await event.respond("Interval must be 1–60.")
            return
        CONFIG["interval"] = n
        save_config(CONFIG)
        await event.respond(f"Interval set to {n} min.")

    # --- /on --------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/on$"))
    @_owner_only(rt)
    async def _on(event):
        if not rt.user_client:
            await event.respond("No user session. Use /login (or Account → QR Login) first.")
            return
        CONFIG["clock_on"] = True
        save_config(CONFIG)
        CLK.start_clock(rt.user_client)
        await event.respond("Clock ON.", buttons=P.main_panel_buttons())

    # --- /off -------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/off$"))
    @_owner_only(rt)
    async def _off(event):
        if not rt.user_client:
            await event.respond("No user session.")
            return
        await CLK.stop_clock(rt.user_client)
        await event.respond("Clock OFF.", buttons=P.main_panel_buttons())

    # --- /status ----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/status$"))
    @_owner_only(rt)
    async def _status(event):
        await event.respond(P.status_text(), buttons=P.status_buttons())

    # --- /memory ----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/memory$"))
    @_owner_only(rt)
    async def _memory(event):
        await event.respond(P.memory_text(), buttons=P.memory_buttons())

    # --- .rep -------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^\.rep\s+(\d+)\s+(\d+)\s+(.+)$"))
    @_owner_only(rt)
    async def _rep(event):
        if event.chat_id != rt.owner_id:
            return
        interval = int(event.pattern_match.group(1))
        duration = int(event.pattern_match.group(2))
        text = event.pattern_match.group(3).strip()
        ok, res = J.create_job(rt.owner_id, event.chat_id, interval, duration, text)
        if not ok:
            if res == "insufficient":
                bal = E.get_balance(rt.owner_id)
                await event.respond(P.t("insufficient", need=E.cost_job(), have=bal))
            else:
                await event.respond(res)
            return
        job = res
        task = asyncio.create_task(J.run_job(rt.bot_client, job))
        J.register_task(job["id"], task)
        await event.respond(P.t("job_created", id=job["id"], every=interval, duration=duration))

    # --- .stop ------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^\.stop$"))
    @_owner_only(rt)
    async def _stop(event):
        if event.chat_id != rt.owner_id:
            return
        n = J.stop_jobs_in_chat(event.chat_id)
        await event.respond(f"{P.t('job_stopped')} ({n})")

    # --- callback router --------------------------------------------------
    @bot.on(events.CallbackQuery())
    @_guarded(rt)
    async def _cb(event):
        await _safe_answer(event)
        data = event.data.decode() if event.data else ""
        try:
            await _route_callback(rt, event, data)
        except (QueryIdInvalidError, MessageNotModifiedError):
            pass
        except Exception as e:
            log_bot.warning(f"callback error data={data}: {e}")

    # --- text router (for awaiting inputs) --------------------------------
    @bot.on(events.NewMessage())
    @_guarded(rt)
    async def _text(event):
        if event.sender_id != rt.owner_id:
            return
        if event.raw_text.startswith(("/", ".")):
            return
        state = rt._awaiting

        if state.get("phone"):
            state.pop("phone", None)
            rt.save_state()
            rt._pending_targets["phone"] = event.raw_text.strip()
            rt.save_state()
            await _do_login(rt, event)
            return
        if state.get("code"):
            state.pop("code", None)
            rt.save_state()
            rt._pending_targets["code"] = event.raw_text.strip()
            rt.save_state()
            await _do_login(rt, event)
            return
        if state.get("password"):
            state.pop("password", None)
            rt.save_state()
            rt._pending_targets["password"] = event.raw_text.strip()
            rt.save_state()
            await _do_login(rt, event)
            return
        if state.get("interval"):
            state.pop("interval", None)
            rt.save_state()
            try:
                n = int(event.raw_text.strip())
                if 1 <= n <= 60:
                    CONFIG["interval"] = n
                    save_config(CONFIG)
                    await event.respond(f"Interval set to {n} min.")
                else:
                    await event.respond("Must be 1–60.")
            except Exception:
                await event.respond("Invalid number.")
            return
        if state.get("timezone"):
            state.pop("timezone", None)
            rt.save_state()
            zone = event.raw_text.strip()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(zone)
                CONFIG["timezone"] = zone
                save_config(CONFIG)
                await event.respond(f"Timezone set to `{zone}`.")
            except Exception:
                await event.respond("Invalid timezone.")
            return
        if state.get("base_name"):
            state.pop("base_name", None)
            rt.save_state()
            CONFIG["base_name"] = event.raw_text.strip()
            save_config(CONFIG)
            await event.respond("Base name updated.")
            return
        if state.get("custom_name_font"):
            state.pop("custom_name_font", None)
            rt.save_state()
            txt = event.raw_text.strip()
            if len(txt) < 26:
                await event.respond("Need at least 26 characters (A–Z).")
            else:
                CONFIG["custom_name_font"] = txt[:26]
                save_config(CONFIG)
                await event.respond("Custom name font saved.")
            return
        if state.get("custom_clock_font"):
            state.pop("custom_clock_font", None)
            rt.save_state()
            txt = event.raw_text.strip()
            if len(txt) < 10:
                await event.respond("Need at least 10 characters (0–9).")
            else:
                CONFIG["custom_clock_font"] = txt[:10]
                save_config(CONFIG)
                await event.respond("Custom clock font saved.")
            return
        if state.get("reqdiamonds"):
            state.pop("reqdiamonds", None)
            rt.save_state()
            try:
                amount = int(event.raw_text.strip())
            except Exception:
                await event.respond("Invalid amount.")
                return
            req = R.create_diamond_request(rt.owner_id, amount)
            await event.respond(f"Request sent: `{req['id']}`")


# ===========================================================================
# Callback router
# ===========================================================================
async def _route_callback(rt: BotRuntime, event, data: str) -> None:
    bot = rt.bot_client

    async def edit(text: str, buttons=None):
        try:
            await event.edit(text, buttons=buttons, parse_mode="md")
        except MessageNotModifiedError:
            pass
        except Exception:
            try:
                await bot.send_message(event.chat_id, text, buttons=buttons)
            except Exception:
                pass

    # navigation
    if data == "nav:main":
        await edit(P.main_panel_text(), P.main_panel_buttons()); return
    if data == "nav:status":
        await edit(P.status_text(), P.status_buttons()); return
    if data == "nav:settings":
        await edit(P.settings_text(), P.settings_buttons()); return
    if data == "nav:appearance":
        await edit(P.appearance_text(), P.appearance_buttons()); return
    if data == "nav:jobs":
        await edit(P.jobs_text(), P.jobs_buttons()); return
    if data == "nav:account":
        await edit(P.account_text(), P.account_buttons()); return
    if data == "nav:memory":
        await edit(P.memory_text(), P.memory_buttons()); return
    if data == "nav:help":
        await edit(P.help_text(), P.help_buttons()); return

    # clock on/off
    if data == "clock:on":
        if not rt.user_client:
            await _safe_answer(event, "No user session.", alert=True); return
        CONFIG["clock_on"] = True
        save_config(CONFIG)
        CLK.start_clock(rt.user_client)
        await edit(P.main_panel_text(), P.main_panel_buttons()); return
    if data == "clock:off":
        if not rt.user_client:
            await _safe_answer(event, "No user session.", alert=True); return
        await CLK.stop_clock(rt.user_client)
        await edit(P.main_panel_text(), P.main_panel_buttons()); return

    # settings
    if data == "set:interval":
        rt._awaiting["interval"] = True; rt.save_state()
        await edit("Send interval (1–60):", [[Button.inline(P.t("btn_back"), b"nav:settings")]]); return
    if data == "set:timezone":
        rt._awaiting["timezone"] = True; rt.save_state()
        await edit("Send timezone (e.g. Europe/Berlin):", [[Button.inline(P.t("btn_back"), b"nav:settings")]]); return
    if data == "set:language":
        await edit(P.language_text(), P.language_buttons()); return
    if data.startswith("set:lang:"):
        lang = data.split(":")[-1]
        CONFIG["language"] = lang
        save_config(CONFIG)
        await edit(P.language_text(), P.language_buttons()); return

    # appearance
    if data == "app:base":
        rt._awaiting["base_name"] = True; rt.save_state()
        await edit("Send new base name:", [[Button.inline(P.t("btn_back"), b"nav:appearance")]]); return
    if data == "app:namefont":
        rows = [[Button.inline(n, f"app:namefont:{n}".encode())] for n in
                ["normal", "italic", "bold", "script", "fraktur", "double", "sans", "sans-bold"]]
        rows.append([Button.inline(P.t("btn_back"), b"nav:appearance")])
        await edit("Pick a name font:", rows); return
    if data.startswith("app:namefont:"):
        CONFIG["name_font"] = data.split(":")[-1]
        save_config(CONFIG)
        await edit(P.appearance_text(), P.appearance_buttons()); return
    if data == "app:clockfont":
        from .fonts import DIGIT_FONTS
        rows = [[Button.inline(n, f"app:clockfont:{n}".encode())] for n in DIGIT_FONTS.keys()]
        rows.append([Button.inline(P.t("btn_back"), b"nav:appearance")])
        await edit("Pick a clock font:", rows); return
    if data.startswith("app:clockfont:"):
        CONFIG["clock_font"] = data.split(":")[-1]
        save_config(CONFIG)
        await edit(P.appearance_text(), P.appearance_buttons()); return
    if data == "app:customname":
        rt._awaiting["custom_name_font"] = True; rt.save_state()
        await edit("Send 26 characters for A–Z:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]]); return
    if data == "app:customclock":
        rt._awaiting["custom_clock_font"] = True; rt.save_state()
        await edit("Send 10 characters for 0–9:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]]); return

    # jobs
    if data == "job:new":
        await edit(
            "Use `.rep <seconds> <minutes> <text>` in this DM.",
            [[Button.inline(P.t("btn_back"), b"nav:jobs")]],
        ); return
    if data == "job:list":
        await edit(P.jobs_text(), P.jobs_buttons()); return
    if data == "job:stopall":
        n = J.stop_all()
        await edit(f"Stopped {n} jobs.", [[Button.inline(P.t("btn_back"), b"nav:jobs")]]); return
    if data == "job:templates":
        tpls = J.list_templates(rt.owner_id)
        if not tpls:
            await edit("No templates.", [[Button.inline(P.t("btn_back"), b"nav:jobs")]]); return
        rows = [[Button.inline(n, f"job:tpl:{n}".encode())] for n in tpls.keys()]
        rows.append([Button.inline(P.t("btn_back"), b"nav:jobs")])
        await edit("Templates:", rows); return
    if data.startswith("job:tpl:"):
        name = data.split(":", 2)[-1]
        tpl = J.load_template(rt.owner_id, name) or ""
        await edit(f"**{name}**\n\n{tpl}", [[Button.inline(P.t("btn_back"), b"nav:jobs")]]); return

    # account
    if data == "acc:reqdiamonds":
        rt._awaiting["reqdiamonds"] = True; rt.save_state()
        await edit("Send the amount of diamonds (1–10000):",
                   [[Button.inline(P.t("btn_back"), b"nav:account")]]); return
    if data == "acc:reqsub":
        await edit(P.plan_text(), P.plan_buttons()); return
    if data.startswith("reqsub:"):
        plan = data.split(":")[-1]
        R.create_subscription_request(rt.owner_id, plan)
        await edit("Request submitted.", [[Button.inline(P.t("btn_back"), b"nav:account")]]); return
    if data == "acc:myreq":
        items = R.for_user(rt.owner_id)[:20]
        if not items:
            await edit("No requests.", [[Button.inline(P.t("btn_back"), b"nav:account")]]); return
        txt = "\n".join(f"• `{r['id']}` — {r['type']} — {r['status']}" for r in items)
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:account")]]); return
    if data == "acc:referral":
        stats = REF.stats(rt.owner_id)
        await edit(P.t("your_referral", code=stats["code"]),
                   [[Button.inline(P.t("btn_back"), b"nav:account")]]); return
    if data == "acc:notify":
        await edit(P.notify_text(), P.notify_buttons()); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]
        u = U.get_user(rt.owner_id) or {}
        n = (u.get("notify") or {})
        new_val = not bool(n.get(key, True))
        U.set_notify(rt.owner_id, key, new_val)
        await edit(P.notify_text(), P.notify_buttons()); return

    # QR login
    if data == "acc:qrlogin":
        await _handle_qr_login(rt, event); return

    # memory
    if data == "mem:view":
        from .store import memory_store
        mem = memory_store.all()
        txt = "```\n" + (str(mem)[:3500]) + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:clear":
        from .store import memory_store
        memory_store.replace({})
        await edit("Memory cleared.", [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:clearstate":
        rt._awaiting.clear(); rt._pending_targets.clear(); rt._peer_request_open = False
        rt.save_state()
        await edit("State cleared.", [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:dump":
        from .store import memory_store, user_state_store
        payload = {
            "memory": memory_store.all(),
            "state": user_state_store.all(),
        }
        txt = "```json\n" + str(payload)[:3500] + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return


# ===========================================================================
# QR login handler
# ===========================================================================
async def _handle_qr_login(rt: BotRuntime, event) -> None:
    """Start QR login and send the QR image to the owner."""
    bot = rt.bot_client

    try:
        ok, msg, png, expires_at = await QRL.start_qr_login(rt)
    except Exception as e:
        log_bot.error(f"QR login failed: {e}")
        try:
            await bot.send_message(event.chat_id, f"QR login failed: {e}")
        except Exception:
            pass
        return

    if not ok:
        try:
            await bot.send_message(event.chat_id, f"QR login error: {msg}")
        except Exception:
            pass
        return

    try:
        remaining = max(0, int(expires_at - time.time()))
        caption = (
            "🔐 **QR Code Login**\n\n"
            "1. Open **Telegram** on your phone (the official app, not this bot).\n"
            "2. Go to **Settings → Devices → Link Desktop Device**.\n"
            "3. Scan this QR code.\n\n"
            f"⏱ Expires in {remaining} seconds.\n"
            "Send /login again if it expires."
        )
        await bot.send_file(
            event.chat_id,
            io.BytesIO(png),
            caption=caption,
            parse_mode="md",
            force_document=False,
        )
    except Exception as e:
        log_bot.error(f"QR image send failed: {e}")
        try:
            await bot.send_message(event.chat_id, f"Failed to send QR image: {e}")
        except Exception:
            pass


# ===========================================================================
# Login flow (phone + code) — proxy-aware
# ===========================================================================
async def _do_login(rt: BotRuntime, event) -> None:
    phone = rt._pending_targets.get("phone")
    code = rt._pending_targets.get("code")
    password = rt._pending_targets.get("password")

    # Build a proxy-aware client (Xray SOCKS5 on 127.0.0.1:1080)
    if rt.user_client is None:
        try:
            from .bootstrap import make_user_client_for_login
            rt.user_client = await make_user_client_for_login()
        except Exception as e:
            log_bot.error(f"could not create user client: {e}")
            await event.respond(f"Login failed: {e}")
            return

    try:
        if phone and not code:
            await rt.user_client.send_code_request(phone)
            rt._awaiting["code"] = True
            rt.save_state()
            await event.respond("Code sent. Send it with dashes (e.g. 1-2-3-4-5).")
            return
        if code:
            code_clean = code.replace("-", "").replace(" ", "")
            try:
                await rt.user_client.sign_in(phone, code_clean)
            except SessionPasswordNeededError:
                rt._awaiting["password"] = True
                rt.save_state()
                await event.respond("2FA password required. Send it now.")
                return
            rt._pending_targets.clear()
            rt.save_state()
            await event.respond("Logged in successfully.")
            return
        if password:
            await rt.user_client.sign_in(password=password)
            rt._pending_targets.clear()
            rt.save_state()
            await event.respond("Logged in successfully (2FA).")
            return
    except Exception as e:
        log_bot.error(f"login error: {e}")
        await event.respond(f"Login failed: {e}")


# ===========================================================================
# Watchdog
# ===========================================================================
async def _watchdog(rt: BotRuntime) -> None:
    while not rt.stop_event.is_set():
        try:
            if rt.bot_client and not rt.bot_client.is_connected():
                await rt.bot_client.connect()
            if rt.user_client and not rt.user_client.is_connected():
                try:
                    await rt.user_client.connect()
                except Exception:
                    pass
        except Exception as e:
            log_bot.warning(f"watchdog: {e}")
        try:
            await asyncio.wait_for(rt.stop_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass


async def start_watchdog(rt: BotRuntime) -> None:
    rt.watchdog_task = asyncio.create_task(_watchdog(rt))


async def stop_watchdog(rt: BotRuntime) -> None:
    rt.stop_event.set()
    if rt.watchdog_task:
        rt.watchdog_task.cancel()
        try:
            await rt.watchdog_task
        except Exception:
            pass