"""Telegram bot runtime: bot + user clients, all handlers, watchdog.

Login flow uses state["step"] = "idle" | "phone" | "code" | "2fa"
and stores phone + hash in state.

Phone step tries ResendCodeRequest ONCE per session as a non-fatal attempt
to force SMS. Handles SendCodeUnavailableError gracefully.
QR login uses Telethon's native client.qr_login().
"""
import asyncio
import io
import random
import re
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
    from telethon import Button, TelegramClient, events, functions
    from telethon.errors import (
        FloodWaitError, MessageNotModifiedError, QueryIdInvalidError,
        SessionPasswordNeededError, PhoneCodeInvalidError,
        PhoneCodeExpiredError, PhoneNumberInvalidError, PasswordHashInvalidError,
    )
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

        self._state: dict = {
            "step": "idle",
            "phone": None,
            "hash": None,
            "awaiting": {},
            "pending_targets": {},
            "peer_request_open": False,
            "_resend_tried": False,
        }
        self._qr_client = None
        self._qr_token_hex: Optional[str] = None
        self._qr_expires_at: float = 0.0
        self._qr_needs_2fa: bool = False

    @property
    def awaiting(self) -> dict:
        return self._state.setdefault("awaiting", {})

    @property
    def pending_targets(self) -> dict:
        return self._state.setdefault("pending_targets", {})

    def save_state(self) -> None:
        user_state_store.update({
            "login_step": self._state.get("step"),
            "login_phone": self._state.get("phone"),
            "login_hash": self._state.get("hash"),
            "awaiting": self._state.get("awaiting") or {},
            "pending_targets": self._state.get("pending_targets") or {},
            "peer_request_open": self._state.get("peer_request_open", False),
            "_resend_tried": self._state.get("_resend_tried", False),
        })

    def load_state(self) -> None:
        st = user_state_store.all() or {}
        self._state["step"] = st.get("login_step") or "idle"
        self._state["phone"] = st.get("login_phone")
        self._state["hash"] = st.get("login_hash")
        self._state["awaiting"] = st.get("awaiting") or {}
        self._state["pending_targets"] = st.get("pending_targets") or {}
        self._state["peer_request_open"] = bool(st.get("peer_request_open", False))
        self._state["_resend_tried"] = bool(st.get("_resend_tried", False))


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
    except Exception:
        pass


async def _tidy(event) -> None:
    try:
        await event.delete()
    except Exception:
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

    state = rt._state

    def persist():
        rt.save_state()

    # --- /start -----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/start$"))
    @_guarded(rt)
    async def _start(event):
        uid = event.sender_id
        if uid != rt.owner_id:
            await event.respond(
                "Send the pairing code shown in the server logs to become owner."
            )
            return
        await event.respond(P.main_panel_text(), buttons=P.main_panel_buttons())

    # --- pairing ----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^\d{6}$"))
    @_guarded(rt)
    async def _pairing(event):
        if rt.owner_id and rt.owner_id != 0:
            return
        if event.raw_text.strip() == rt.pairing_code:
            rt.owner_id = int(event.sender_id)
            CONFIG["owner_id"] = rt.owner_id
            save_config(CONFIG)
            U.ensure_user(rt.owner_id, first_name="Owner")
            await event.respond("Pairing successful.")
            await event.respond(P.main_panel_text(), buttons=P.main_panel_buttons())
        else:
            await event.respond("Wrong pairing code.")

    # --- /login -----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/login$"))
    @_owner_only(rt)
    async def _login(event):
        authorized = False
        try:
            authorized = bool(rt.user_client and await rt.user_client.is_user_authorized())
        except Exception:
            authorized = False
        if authorized:
            await event.respond("Already logged in.")
            return
        state["step"] = "phone"
        state["phone"] = None
        state["hash"] = None
        state["_resend_tried"] = False
        persist()
        await event.respond("📱 Send your phone number with country code (e.g. +989121234567).")

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
        state["step"] = "idle"
        state["phone"] = None
        state["hash"] = None
        state["awaiting"] = {}
        state["_resend_tried"] = False
        rt._qr_needs_2fa = False
        persist()
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
            lines.append(f"• `{r['id']}` — {r['type']} — {r['status']}")
        await event.respond("\n".join(lines))

    # --- /refer -----------------------------------------------------------
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

    # --- /name ------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/name\s+(.+)$"))
    @_owner_only(rt)
    async def _name(event):
        base = event.pattern_match.group(1).strip()
        CONFIG["base_name"] = base
        save_config(CONFIG)
        if rt.user_client:
            await CLK.write_base_name_sync(rt.user_client)
        await event.respond(f"Base name set to `{base}`.")

    # --- /font ------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/font\s+(\S+)$"))
    @_owner_only(rt)
    async def _font(event):
        name = event.pattern_match.group(1)
        CONFIG["name_font"] = name
        save_config(CONFIG)
        await event.respond(f"Name font set to `{name}`.")

    # --- /clockfont -------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/clockfont\s+(\S+)$"))
    @_owner_only(rt)
    async def _clockfont(event):
        name = event.pattern_match.group(1)
        CONFIG["clock_font"] = name
        save_config(CONFIG)
        await event.respond(f"Clock font set to `{name}`.")

    # --- /tz --------------------------------------------------------------
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

    # --- /interval --------------------------------------------------------
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
            await event.respond("No user session. Use /login or QR Login.")
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

    # --- callbacks --------------------------------------------------------
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

    # --- text router ------------------------------------------------------
    @bot.on(events.NewMessage())
    @_guarded(rt)
    async def _text(event):
        if event.sender_id != rt.owner_id:
            return
        if event.raw_text.startswith(("/", ".")):
            return

        text = (event.raw_text or "").strip()
        step = state.get("step") or "idle"
        awaiting = state.get("awaiting") or {}

        # --- QR 2FA ---
        if awaiting.get("qr_2fa_password"):
            awaiting.pop("qr_2fa_password", None)
            persist()
            try:
                from .qr_login import finish_qr_2fa
                ok, msg = await finish_qr_2fa(rt, text)
                if ok:
                    await event.respond("✅ Logged in. Clock started.")
                else:
                    await event.respond(f"❌ {msg}")
            except Exception as e:
                log_bot.error(f"qr 2fa failed: {e}")
                await event.respond(f"❌ 2FA failed: {e}")
            return

        # --- LOGIN: phone step ---
        if step == "phone":
            phone = re.sub(r"[^\d+]", "", text)
            if not phone:
                await event.respond("Send your phone number with country code.")
                return
            try:
                if rt.user_client is None:
                    from .bootstrap import make_user_client_for_login
                    rt.user_client = await make_user_client_for_login()

                # Step 1: initial code request
                sent = await rt.user_client.send_code_request(phone)
                phone_code_hash = sent.phone_code_hash
                log_bot.info(
                    f"send_code_request OK for {phone} "
                    f"(type={type(sent).__name__})"
                )

                # Step 2: try ResendCodeRequest ONCE per session only.
                # If SendCodeUnavailableError, we just stop and use app code.
                if not state.get("_resend_tried"):
                    state["_resend_tried"] = True
                    try:
                        sms_result = await rt.user_client(functions.auth.ResendCodeRequest(
                            phone_number=phone,
                            phone_code_hash=phone_code_hash,
                        ))
                        phone_code_hash = sms_result.phone_code_hash
                        log_bot.info(f"ResendCodeRequest OK for {phone}")
                    except Exception as resend_err:
                        err_name = type(resend_err).__name__
                        if "SendCodeUnavailable" in err_name:
                            log_bot.info(
                                f"SendCodeUnavailable — all delivery options exhausted, "
                                f"using app code only"
                            )
                        else:
                            log_bot.warning(
                                f"ResendCodeRequest failed (non-fatal): "
                                f"{err_name}: {resend_err}"
                            )

                state["step"] = "code"
                state["phone"] = phone
                state["hash"] = phone_code_hash
                persist()
                await event.respond(
                    "📨 Code sent.\n\n"
                    "Check **Telegram app** → chat with **Telegram** (blue checkmark).\n"
                    "If not there, check SMS.\n\n"
                    "Send the code with dashes, e.g. `1-2-3-4-5`."
                )
            except PhoneNumberInvalidError:
                await event.respond("Invalid phone number.")
            except FloodWaitError as e:
                await event.respond(f"Rate-limited; wait {e.seconds}s.")
            except ConnectionError:
                await event.respond("❌ Connection lost.")
                try:
                    if rt.user_client:
                        await rt.user_client.connect()
                except Exception:
                    pass
            except Exception as e:
                log_bot.error(f"send_code_request failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
            return

        # --- LOGIN: code step ---
        if step == "code":
            code = re.sub(r"\D", "", text)
            await _tidy(event)
            try:
                await rt.user_client.sign_in(
                    state["phone"], code, phone_code_hash=state["hash"]
                )
            except SessionPasswordNeededError:
                state["step"] = "2fa"
                persist()
                await event.respond("🔐 Send your 2FA password.")
                return
            except PhoneCodeInvalidError:
                await event.respond("Wrong code. Try again.")
                return
            except PhoneCodeExpiredError:
                state["step"] = "idle"
                state["phone"] = None
                state["hash"] = None
                state["_resend_tried"] = False
                persist()
                await event.respond("Code expired. Use /login again.")
                return
            except ConnectionError:
                await event.respond("❌ Connection lost.")
                return
            except Exception as e:
                log_bot.error(f"sign_in failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
                return
            await _finish_login(rt, event, state, persist)
            return

        # --- LOGIN: 2FA step ---
        if step == "2fa":
            await _tidy(event)
            try:
                await rt.user_client.sign_in(password=text)
            except PasswordHashInvalidError:
                await event.respond("Wrong password.")
                return
            except ConnectionError:
                await event.respond("❌ Connection lost.")
                return
            except Exception as e:
                log_bot.error(f"2fa sign_in failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
                return
            await _finish_login(rt, event, state, persist)
            return

        # --- AWAITING FLOW ---
        if awaiting.get("interval"):
            awaiting.pop("interval", None); persist()
            try:
                n = int(text)
                if 1 <= n <= 60:
                    CONFIG["interval"] = n
                    save_config(CONFIG)
                    await event.respond(f"Interval set to {n} min.")
                else:
                    await event.respond("Must be 1–60.")
            except Exception:
                await event.respond("Invalid number.")
            return

        if awaiting.get("timezone"):
            awaiting.pop("timezone", None); persist()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(text)
                CONFIG["timezone"] = text
                save_config(CONFIG)
                await event.respond(f"Timezone set to `{text}`.")
            except Exception:
                await event.respond("Invalid timezone.")
            return

        if awaiting.get("base_name"):
            awaiting.pop("base_name", None); persist()
            CONFIG["base_name"] = text
            save_config(CONFIG)
            await event.respond("Base name updated.")
            return

        if awaiting.get("custom_name_font"):
            awaiting.pop("custom_name_font", None); persist()
            if len(text) < 26:
                await event.respond("Need at least 26 characters (A–Z).")
            else:
                CONFIG["custom_name_font"] = text[:26]
                save_config(CONFIG)
                await event.respond("Custom name font saved.")
            return

        if awaiting.get("custom_clock_font"):
            awaiting.pop("custom_clock_font", None); persist()
            if len(text) < 10:
                await event.respond("Need at least 10 characters (0–9).")
            else:
                CONFIG["custom_clock_font"] = text[:10]
                save_config(CONFIG)
                await event.respond("Custom clock font saved.")
            return

        if awaiting.get("reqdiamonds"):
            awaiting.pop("reqdiamonds", None); persist()
            try:
                amount = int(text)
            except Exception:
                await event.respond("Invalid amount.")
                return
            req = R.create_diamond_request(rt.owner_id, amount)
            await event.respond(f"Request sent: `{req['id']}`")
            return


async def _finish_login(rt: BotRuntime, event, state: dict, persist) -> None:
    state["step"] = "idle"
    state["phone"] = None
    state["hash"] = None
    state["_resend_tried"] = False
    persist()
    try:
        me = await rt.user_client.get_me()
        log_bot.info(f"Logged in as {me.first_name} ({me.id})")
    except Exception:
        pass
    await event.respond("✅ Logged in. Clock started.")
    try:
        if CONFIG.get("clock_on") and rt.user_client:
            CLK.start_clock(rt.user_client)
    except Exception:
        pass


# ===========================================================================
# Callback router
# ===========================================================================
async def _route_callback(rt: BotRuntime, event, data: str) -> None:
    bot = rt.bot_client
    state = rt._state

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
        rt.awaiting["interval"] = True; rt.save_state()
        await edit("Send interval (1–60):", [[Button.inline(P.t("btn_back"), b"nav:settings")]]); return
    if data == "set:timezone":
        rt.awaiting["timezone"] = True; rt.save_state()
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
        rt.awaiting["base_name"] = True; rt.save_state()
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
        rt.awaiting["custom_name_font"] = True; rt.save_state()
        await edit("Send 26 characters for A–Z:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]]); return
    if data == "app:customclock":
        rt.awaiting["custom_clock_font"] = True; rt.save_state()
        await edit("Send 10 characters for 0–9:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]]); return

    # jobs
    if data == "job:new":
        await edit("Use `.rep <seconds> <minutes> <text>` in this DM.",
                   [[Button.inline(P.t("btn_back"), b"nav:jobs")]]); return
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
        rt.awaiting["reqdiamonds"] = True; rt.save_state()
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
        txt = "```\n" + (str(memory_store.all())[:3500]) + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:clear":
        from .store import memory_store
        memory_store.replace({})
        await edit("Memory cleared.", [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:clearstate":
        state["step"] = "idle"
        state["phone"] = None
        state["hash"] = None
        state["awaiting"] = {}
        state["pending_targets"] = {}
        state["_resend_tried"] = False
        rt.save_state()
        await edit("State cleared.", [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return
    if data == "mem:dump":
        from .store import memory_store, user_state_store
        payload = {"memory": memory_store.all(), "state": user_state_store.all()}
        txt = "```json\n" + str(payload)[:3500] + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]]); return


# ===========================================================================
# QR login
# ===========================================================================
async def _handle_qr_login(rt: BotRuntime, event) -> None:
    bot = rt.bot_client

    try:
        from .qr_login import diagnose
        d = diagnose()
        if not d["telethon"]:
            await bot.send_message(
                event.chat_id,
                f"❌ QR Login unavailable.\nTelethon error: `{d['telethon_error']}`"
            )
            return
        if not d["qrcode"]:
            await bot.send_message(
                event.chat_id,
                f"❌ QR Login unavailable.\nqrcode error: `{d['qrcode_error']}`"
            )
            return
    except Exception as e:
        log_bot.error(f"qr diagnose failed: {type(e).__name__}: {e}")

    try:
        ok, msg, png, expires_at = await QRL.start_qr_login(rt)
    except Exception as e:
        log_bot.error(f"QR login raised: {type(e).__name__}: {e}")
        try:
            await bot.send_message(event.chat_id, f"QR login failed: {type(e).__name__}: {e}")
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
            "1. Open **Telegram** on your phone (official app, not this bot).\n"
            "2. Go to **Settings → Devices → Link Desktop Device**.\n"
            "3. Scan this QR code.\n\n"
            f"⏱ Expires in {remaining} seconds.\n"
            "If it expires, send /login again."
        )
        await bot.send_file(
            event.chat_id,
            io.BytesIO(png),
            caption=caption,
            parse_mode="md",
            force_document=False,
        )
    except Exception as e:
        log_bot.error(f"QR image send failed: {type(e).__name__}: {e}")
        try:
            await bot.send_message(event.chat_id, f"Failed to send QR image: {e}")
        except Exception:
            pass


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