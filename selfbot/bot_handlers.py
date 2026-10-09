"""Telegram bot runtime: bot + user clients, all handlers, watchdog.

Two roles:
  * Owner (rt.owner_id) — full control panel
  * Regular user         — user panel + web panel access

Login flow uses state["step"] = "idle" | "phone" | "code" | "2fa".
Phone step tries ResendCodeRequest ONCE per session (non-fatal).
QR login uses Telethon's native client.qr_login().
Job creation is a graphical step-by-step flow.
"""
import asyncio
import io
import os
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
            "job_draft": {},
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
            "job_draft": self._state.get("job_draft") or {},
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
        self._state["job_draft"] = st.get("job_draft") or {}


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
    """Check ban + rate-limit. Do NOT block non-owner users."""
    def deco(fn):
        async def wrapper(event, *a, **kw):
            uid = event.sender_id or 0
            if uid and U.is_banned(uid):
                return
            if uid and uid != runtime.owner_id:
                if not user_limiter.allow(uid):
                    try:
                        await event.respond("Rate limited. Please slow down.")
                    except Exception:
                        pass
                    return
                try:
                    U.touch(uid)
                except Exception:
                    pass
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


def _public_base_url() -> str:
    """Return base URL for the web panel."""
    base = os.environ.get("PUBLIC_URL") or ""
    if base:
        return base.rstrip("/")
    dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if dom:
        return f"https://{dom}"
    return "http://localhost:8080"


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
    @bot.on(events.NewMessage(pattern=r"^/start$", incoming=True))
    @_guarded(rt)
    async def _start(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        if uid == rt.owner_id:
            await event.respond(P.main_panel_text(), buttons=P.main_panel_buttons())
        else:
            await event.respond(P.user_panel_text(uid), buttons=P.user_panel_buttons(uid))

    # --- /help ------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/help$", incoming=True))
    @_guarded(rt)
    async def _help(event):
        uid = event.sender_id
        if uid == rt.owner_id:
            await event.respond(P.help_text(), buttons=P.help_buttons())
        else:
            await event.respond(P.user_help_text(), buttons=P.user_help_buttons())

    # --- pairing (only if owner is NOT set) -------------------------------
    @bot.on(events.NewMessage(pattern=r"^\d{6}$", incoming=True))
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
    @bot.on(events.NewMessage(pattern=r"^/login$", incoming=True))
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
    @bot.on(events.NewMessage(pattern=r"^/logout$", incoming=True))
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
        state["job_draft"] = {}
        rt._qr_needs_2fa = False
        persist()
        await event.respond("Logged out and session deleted.")

    # --- /account ---------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/account$", incoming=True))
    @_guarded(rt)
    async def _account(event):
        uid = event.sender_id
        if uid == rt.owner_id:
            await event.respond(P.account_text(), buttons=P.account_buttons())
        else:
            await event.respond(P.user_account_text(uid),
                                buttons=P.user_account_buttons())

    # --- /request ---------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/request$", incoming=True))
    @_guarded(rt)
    async def _request(event):
        uid = event.sender_id
        if uid == rt.owner_id:
            await event.respond(P.account_text(), buttons=P.account_buttons())
        else:
            await event.respond(P.user_account_text(uid),
                                buttons=P.user_account_buttons())

    # --- /myrequests ------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/myrequests$", incoming=True))
    @_guarded(rt)
    async def _myreq(event):
        uid = event.sender_id
        items = R.for_user(uid)[:20]
        if not items:
            await event.respond("No requests yet.")
            return
        lines = ["**My requests**"]
        for r in items:
            lines.append(f"• `{r['id']}` — {r['type']} — {r['status']}")
        await event.respond("\n".join(lines))

    # --- /refer -----------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/refer(?:\s+(\S+))?$", incoming=True))
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

    # --- owner-only commands ---------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/name\s+(.+)$", incoming=True))
    @_owner_only(rt)
    async def _name(event):
        base = event.pattern_match.group(1).strip()
        CONFIG["base_name"] = base
        save_config(CONFIG)
        if rt.user_client:
            await CLK.write_base_name_sync(rt.user_client)
        await event.respond(f"Base name set to `{base}`.")

    @bot.on(events.NewMessage(pattern=r"^/font\s+(\S+)$", incoming=True))
    @_owner_only(rt)
    async def _font(event):
        name = event.pattern_match.group(1)
        CONFIG["name_font"] = name
        save_config(CONFIG)
        await event.respond(f"Name font set to `{name}`.")

    @bot.on(events.NewMessage(pattern=r"^/clockfont\s+(\S+)$", incoming=True))
    @_owner_only(rt)
    async def _clockfont(event):
        name = event.pattern_match.group(1)
        CONFIG["clock_font"] = name
        save_config(CONFIG)
        await event.respond(f"Clock font set to `{name}`.")

    @bot.on(events.NewMessage(pattern=r"^/tz\s+(\S+)$", incoming=True))
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

    @bot.on(events.NewMessage(pattern=r"^/interval\s+(\d+)$", incoming=True))
    @_owner_only(rt)
    async def _interval(event):
        n = int(event.pattern_match.group(1))
        if n < 1 or n > 60:
            await event.respond("Interval must be 1–60.")
            return
        CONFIG["interval"] = n
        save_config(CONFIG)
        await event.respond(f"Interval set to {n} min.")

    @bot.on(events.NewMessage(pattern=r"^/on$", incoming=True))
    @_owner_only(rt)
    async def _on(event):
        if not rt.user_client:
            await event.respond("No user session. Use /login or QR Login.")
            return
        CONFIG["clock_on"] = True
        save_config(CONFIG)
        CLK.start_clock(rt.user_client)
        await event.respond("Clock ON.", buttons=P.main_panel_buttons())

    @bot.on(events.NewMessage(pattern=r"^/off$", incoming=True))
    @_owner_only(rt)
    async def _off(event):
        if not rt.user_client:
            await event.respond("No user session.")
            return
        await CLK.stop_clock(rt.user_client)
        await event.respond("Clock OFF.", buttons=P.main_panel_buttons())

    @bot.on(events.NewMessage(pattern=r"^/status$", incoming=True))
    @_guarded(rt)
    async def _status(event):
        uid = event.sender_id
        if uid == rt.owner_id:
            await event.respond(P.status_text(), buttons=P.status_buttons())
        else:
            await event.respond(P.user_account_text(uid),
                                buttons=P.user_account_buttons())

    @bot.on(events.NewMessage(pattern=r"^/memory$", incoming=True))
    @_owner_only(rt)
    async def _memory(event):
        await event.respond(P.memory_text(), buttons=P.memory_buttons())

    # --- .rep / .stop -----------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^\.rep\s+(\d+)\s+(\d+)\s+(.+)$", incoming=True))
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

    @bot.on(events.NewMessage(pattern=r"^\.stop$", incoming=True))
    @_owner_only(rt)
    async def _stop(event):
        if event.chat_id != rt.owner_id:
            return
        n = J.stop_jobs_in_chat(event.chat_id)
        await event.respond(f"{P.t('job_stopped')} ({n})")

    # --- raw peer-selection handler ---------------------------------------
    @bot.on(events.Raw)
    async def _raw_peer_selection(update):
        try:
            msg = getattr(update, "message", None)
            if msg is None:
                return
            action = getattr(msg, "action", None)
            if action is None:
                return
            peers = getattr(action, "peers", None)
            if not peers:
                return
            peer = peers[0]
            chat_id = (
                getattr(peer, "chat_id", None)
                or getattr(peer, "channel_id", None)
            )
            if chat_id is None:
                return

            draft = state.setdefault("job_draft", {})
            draft["target"] = chat_id
            rt.save_state()

            try:
                await bot.send_message(
                    rt.owner_id,
                    "Keyboard closed.",
                    buttons=Button.clear(),
                )
            except Exception:
                pass

            await bot.send_message(
                rt.owner_id,
                f"✅ Group selected: `{chat_id}`\n\nNow choose the interval.",
                buttons=P.job_setup_buttons(),
                parse_mode="md",
            )
        except Exception as e:
            log_bot.error(f"peer selection handler error: {e}")

    # --- callbacks --------------------------------------------------------
    @bot.on(events.CallbackQuery())
    async def _cb(event):
        uid = event.sender_id
        if uid and uid != rt.owner_id:
            if not user_limiter.allow(uid):
                await _safe_answer(event, "Rate limited.", alert=True)
                return
        await _safe_answer(event)
        data = event.data.decode() if event.data else ""
        try:
            await _route_callback(rt, event, data)
        except (QueryIdInvalidError, MessageNotModifiedError):
            pass
        except Exception as e:
            log_bot.warning(f"callback error data={data}: {e}")

    # --- text router (fallback for awaiting inputs) -----------------------
    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def _text(event):
        text = (event.raw_text or "").strip()
        if not text:
            return
        if text.startswith("/"):
            return

        # === NON-OWNER user flow ===
        if event.sender_id != rt.owner_id:
            awaiting = state.get("awaiting") or {}
            user_key = f"user_reqdiamonds:{event.sender_id}"
            if awaiting.get(user_key):
                awaiting.pop(user_key, None)
                persist()
                try:
                    amount = int(text)
                    if not (1 <= amount <= 10000):
                        raise ValueError
                except Exception:
                    await event.respond("Invalid amount. Send a number 1–10000.")
                    return
                req = R.create_diamond_request(event.sender_id, amount)
                await event.respond(f"✅ Request `{req['id']}` sent to admin.")
            return

        # === OWNER flow ===
        if text.startswith("."):
            return

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

        # --- JOB DRAFT: custom interval ---
        if awaiting.get("job_interval_custom"):
            awaiting.pop("job_interval_custom", None)
            persist()
            try:
                n = int(text)
                if n < 60:
                    await event.respond("Min 60 seconds.")
                    return
                draft = state.setdefault("job_draft", {})
                draft["interval"] = n
                rt.save_state()
                await event.respond(P.job_duration_text(n),
                                    buttons=P.job_duration_buttons(n),
                                    parse_mode="md")
            except Exception:
                await event.respond("Invalid number.")
            return

        # --- JOB DRAFT: custom duration ---
        if awaiting.get("job_duration_custom"):
            awaiting.pop("job_duration_custom", None)
            persist()
            try:
                n = int(text)
                if n < 1:
                    await event.respond("Min 1 minute.")
                    return
                draft = state.setdefault("job_draft", {})
                draft["duration"] = n
                rt.save_state()
                await event.respond(P.job_text_prompt(),
                                    buttons=P.job_text_buttons(),
                                    parse_mode="md")
            except Exception:
                await event.respond("Invalid number.")
            return

        # --- JOB DRAFT: message text ---
        if awaiting.get("job_text"):
            awaiting.pop("job_text", None)
            persist()
            draft = state.setdefault("job_draft", {})
            draft["text"] = text
            rt.save_state()
            await event.respond(P.job_confirm_text(draft),
                                buttons=P.job_confirm_buttons(draft),
                                parse_mode="md")
            return

        # --- JOB DRAFT: .txt file ---
        if awaiting.get("job_text_file") and event.document:
            awaiting.pop("job_text_file", None)
            persist()
            try:
                data_bytes = await event.download_media(bytes)
                file_text = data_bytes.decode("utf-8", errors="ignore")[:4000]
                draft = state.setdefault("job_draft", {})
                draft["text"] = file_text
                rt.save_state()
                await event.respond(P.job_confirm_text(draft),
                                    buttons=P.job_confirm_buttons(draft),
                                    parse_mode="md")
            except Exception as e:
                await event.respond(f"❌ Could not read file: {e}")
            return

        # --- LOGIN: phone ---
        if step == "phone":
            phone = re.sub(r"[^\d+]", "", text)
            if not phone:
                await event.respond("Send your phone number with country code.")
                return
            try:
                if rt.user_client is None:
                    from .bootstrap import make_user_client_for_login
                    rt.user_client = await make_user_client_for_login()

                sent = await rt.user_client.send_code_request(phone)
                phone_code_hash = sent.phone_code_hash
                log_bot.info(f"send_code_request OK for {phone} (type={type(sent).__name__})")

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
                            log_bot.info("SendCodeUnavailable — app code only")
                        else:
                            log_bot.warning(f"ResendCodeRequest failed: {err_name}: {resend_err}")

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

        # --- LOGIN: code ---
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

        # --- LOGIN: 2FA ---
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

        # --- AWAITING: interval ---
        if awaiting.get("interval"):
            awaiting.pop("interval", None)
            persist()
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

        # --- AWAITING: timezone ---
        if awaiting.get("timezone"):
            awaiting.pop("timezone", None)
            persist()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(text)
                CONFIG["timezone"] = text
                save_config(CONFIG)
                await event.respond(f"Timezone set to `{text}`.")
            except Exception:
                await event.respond("Invalid timezone.")
            return

        # --- AWAITING: base name ---
        if awaiting.get("base_name"):
            awaiting.pop("base_name", None)
            persist()
            CONFIG["base_name"] = text
            save_config(CONFIG)
            await event.respond("Base name updated.")
            return

        # --- AWAITING: custom name font ---
        if awaiting.get("custom_name_font"):
            awaiting.pop("custom_name_font", None)
            persist()
            if len(text) < 26:
                await event.respond("Need at least 26 characters (A–Z).")
            else:
                CONFIG["custom_name_font"] = text[:26]
                save_config(CONFIG)
                await event.respond("Custom name font saved.")
            return

        # --- AWAITING: custom clock font ---
        if awaiting.get("custom_clock_font"):
            awaiting.pop("custom_clock_font", None)
            persist()
            if len(text) < 10:
                await event.respond("Need at least 10 characters (0–9).")
            else:
                CONFIG["custom_clock_font"] = text[:10]
                save_config(CONFIG)
                await event.respond("Custom clock font saved.")
            return

        # --- AWAITING: request diamonds (owner) ---
        if awaiting.get("reqdiamonds"):
            awaiting.pop("reqdiamonds", None)
            persist()
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
    uid = event.sender_id

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

    # ===== USER (non-owner) callbacks =====
    if data.startswith("user:") and uid != rt.owner_id:

        if data == "user:home":
            await edit(P.user_panel_text(uid), P.user_panel_buttons(uid))
            return

        if data == "user:webpanel":
            try:
                from .user_panel import issue_web_token
                tok = issue_web_token(uid)
                url = f"{_public_base_url()}/user/?token={tok}"
                await edit(
                    "**🌐 Your Web Panel**\n\n"
                    "Open the panel below to manage your account, "
                    "request diamonds or subscription, and view your history.\n\n"
                    "_Keep the link private — anyone with it can access your account._",
                    [
                        [Button.url("Open Web Panel", url)],
                        [Button.inline("◀️ Back", b"user:home")],
                    ],
                )
            except Exception as e:
                log_bot.error(f"webpanel error: {type(e).__name__}: {e}")
                await edit(f"❌ Could not generate link: {e}",
                           [[Button.inline("◀️ Back", b"user:home")]])
            return

        if data == "user:account":
            await edit(P.user_account_text(uid), P.user_account_buttons())
            return

        if data == "user:reqdiamonds":
            rt.awaiting[f"user_reqdiamonds:{uid}"] = True
            rt.save_state()
            await edit(
                "**💎 Request diamonds**\n\n"
                "Send the number of diamonds you want (1–10000):",
                [[Button.inline("◀️ Back", b"user:account")]],
            )
            return

        if data == "user:reqsub":
            rows = [
                [Button.inline("Basic — 30 days", b"user:reqsub:basic")],
                [Button.inline("Pro — 30 days", b"user:reqsub:pro")],
                [Button.inline("VIP — 30 days", b"user:reqsub:vip")],
                [Button.inline("◀️ Back", b"user:account")],
            ]
            await edit("**⭐ Request subscription**\n\nPick a plan:", rows)
            return

        if data.startswith("user:reqsub:"):
            plan = data.split(":")[-1]
            R.create_subscription_request(uid, plan)
            await edit(
                f"✅ Subscription request sent to admin.\n\nPlan: **{plan}**",
                [[Button.inline("◀️ Back", b"user:account")]],
            )
            return

        if data == "user:myreq":
            items = R.for_user(uid)[:20]
            if not items:
                await edit(
                    "**📜 My requests**\n\n_You have no requests._",
                    [[Button.inline("◀️ Back", b"user:home")]],
                )
                return
            lines = ["**📜 My requests**\n"]
            for r in items:
                lines.append(f"• `{r['id']}` — {r['type']} — {r['status']}")
            await edit("\n".join(lines),
                       [[Button.inline("◀️ Back", b"user:home")]])
            return

        if data == "user:referral":
            stats = REF.stats(uid)
            await edit(
                f"**🤝 My referral code**\n\n"
                f"`{stats['code']}`\n\n"
                f"Share this code with friends. You both get diamonds when they join.\n\n"
                f"**Total referrals:** {stats['count']}",
                [[Button.inline("◀️ Back", b"user:home")]],
            )
            return

        if data == "user:help":
            await edit(P.user_help_text(), P.user_help_buttons())
            return

        return

    # ===== OWNER callbacks =====
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

    if data == "set:interval":
        rt.awaiting["interval"] = True
        rt.save_state()
        await edit("Send interval (1–60):",
                   [[Button.inline(P.t("btn_back"), b"nav:settings")]])
        return
    if data == "set:timezone":
        rt.awaiting["timezone"] = True
        rt.save_state()
        await edit("Send timezone (e.g. Europe/Berlin):",
                   [[Button.inline(P.t("btn_back"), b"nav:settings")]])
        return
    if data == "set:language":
        await edit(P.language_text(), P.language_buttons()); return
    if data.startswith("set:lang:"):
        lang = data.split(":")[-1]
        CONFIG["language"] = lang
        save_config(CONFIG)
        await edit(P.language_text(), P.language_buttons()); return

    if data == "app:base":
        rt.awaiting["base_name"] = True
        rt.save_state()
        await edit("Send new base name:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]])
        return
    if data == "app:namefont":
        rows = [[Button.inline(n, f"app:namefont:{n}".encode())] for n in
                ["normal", "italic", "bold", "script", "fraktur", "double", "sans", "sans-bold"]]
        rows.append([Button.inline(P.t("btn_back"), b"nav:appearance")])
        await edit("Pick a name font:", rows)
        return
    if data.startswith("app:namefont:"):
        CONFIG["name_font"] = data.split(":")[-1]
        save_config(CONFIG)
        await edit(P.appearance_text(), P.appearance_buttons()); return
    if data == "app:clockfont":
        from .fonts import DIGIT_FONTS
        rows = [[Button.inline(n, f"app:clockfont:{n}".encode())] for n in DIGIT_FONTS.keys()]
        rows.append([Button.inline(P.t("btn_back"), b"nav:appearance")])
        await edit("Pick a clock font:", rows)
        return
    if data.startswith("app:clockfont:"):
        CONFIG["clock_font"] = data.split(":")[-1]
        save_config(CONFIG)
        await edit(P.appearance_text(), P.appearance_buttons()); return
    if data == "app:customname":
        rt.awaiting["custom_name_font"] = True
        rt.save_state()
        await edit("Send 26 characters for A–Z:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]])
        return
    if data == "app:customclock":
        rt.awaiting["custom_clock_font"] = True
        rt.save_state()
        await edit("Send 10 characters for 0–9:",
                   [[Button.inline(P.t("btn_back"), b"nav:appearance")]])
        return

    if data == "job:new":
        rt._state["job_draft"] = {}
        rt._state["awaiting"] = {}
        rt.save_state()
        await edit(P.job_setup_text(), P.job_setup_buttons())
        return
    if data.startswith("job:interval:"):
        val = data.split(":")[2]
        if val == "custom":
            rt.awaiting["job_interval_custom"] = True
            rt.save_state()
            await edit("**Step 1/3 — Custom interval**\n\nSend a number (seconds, min 60):",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
            return
        try:
            interval = int(val)
        except ValueError:
            return
        draft = rt._state.setdefault("job_draft", {})
        draft["interval"] = interval
        rt.save_state()
        await edit(P.job_duration_text(interval), P.job_duration_buttons(interval))
        return
    if data.startswith("job:duration:"):
        val = data.split(":")[2]
        if val == "custom":
            rt.awaiting["job_duration_custom"] = True
            rt.save_state()
            await edit("**Step 2/3 — Custom duration**\n\nSend a number (minutes):",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
            return
        try:
            duration = int(val)
        except ValueError:
            return
        draft = rt._state.setdefault("job_draft", {})
        draft["duration"] = duration
        rt.save_state()
        await edit(P.job_text_prompt(), P.job_text_buttons())
        return
    if data == "job:text:enter":
        rt.awaiting["job_text"] = True
        rt.save_state()
        await edit("Send the message text now:",
                   [[Button.inline("◀️ Back", b"job:setup:back")]])
        return
    if data == "job:text:upload":
        rt.awaiting["job_text_file"] = True
        rt.save_state()
        await edit("Send a `.txt` file with the message:",
                   [[Button.inline("◀️ Back", b"job:setup:back")]])
        return
    if data == "job:confirm:start":
        draft = rt._state.get("job_draft", {})
        interval = draft.get("interval", 0)
        duration = draft.get("duration", 0)
        text = draft.get("text", "")
        target = draft.get("target")
        if not target:
            await edit("❌ No target selected.",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
            return
        ok, res = J.create_job(rt.owner_id, target, interval, duration, text)
        if ok:
            job = res
            task = asyncio.create_task(J.run_job(rt.bot_client, job))
            J.register_task(job["id"], task)
            rt._state["job_draft"] = {}
            rt.save_state()
            await edit(f"✅ Job `{job['id']}` started.\nEvery {interval}s for {duration} min.",
                       [[Button.inline("📋 Jobs", b"nav:jobs")]])
        else:
            await edit(f"❌ {res}",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
        return
    if data == "job:setup:back":
        rt._state["job_draft"] = {}
        for k in ("job_interval_custom", "job_duration_custom",
                  "job_text", "job_text_file"):
            rt.awaiting.pop(k, None)
        rt.save_state()
        await edit(P.job_setup_text(), P.job_setup_buttons())
        return
    if data == "job:pick_target":
        from .panels import group_selection_reply_buttons, group_selection_text
        kb = group_selection_reply_buttons()
        if kb is None:
            await edit("❌ Peer selection unavailable.",
                       [[Button.inline("◀️ Back", b"nav:jobs")]])
            return
        await bot.send_message(event.chat_id, group_selection_text(), buttons=kb)
        return
    if data == "job:list":
        await edit(P.jobs_text(), P.jobs_buttons()); return
    if data == "job:stopall":
        n = J.stop_all()
        await edit(f"Stopped {n} jobs.",
                   [[Button.inline(P.t("btn_back"), b"nav:jobs")]])
        return
    if data == "job:templates":
        tpls = J.list_templates(rt.owner_id)
        if not tpls:
            await edit("No templates.",
                       [[Button.inline(P.t("btn_back"), b"nav:jobs")]])
            return
        rows = [[Button.inline(n, f"job:tpl:{n}".encode())] for n in tpls.keys()]
        rows.append([Button.inline(P.t("btn_back"), b"nav:jobs")])
        await edit("Templates:", rows)
        return
    if data.startswith("job:tpl:"):
        name = data.split(":", 2)[-1]
        tpl = J.load_template(rt.owner_id, name) or ""
        await edit(f"**{name}**\n\n{tpl}",
                   [[Button.inline(P.t("btn_back"), b"nav:jobs")]])
        return

    if data == "acc:reqdiamonds":
        rt.awaiting["reqdiamonds"] = True
        rt.save_state()
        await edit("Send the amount of diamonds (1–10000):",
                   [[Button.inline(P.t("btn_back"), b"nav:account")]])
        return
    if data == "acc:reqsub":
        await edit(P.plan_text(), P.plan_buttons()); return
    if data.startswith("reqsub:"):
        plan = data.split(":")[-1]
        R.create_subscription_request(rt.owner_id, plan)
        await edit("Request submitted.",
                   [[Button.inline(P.t("btn_back"), b"nav:account")]])
        return
    if data == "acc:myreq":
        items = R.for_user(rt.owner_id)[:20]
        if not items:
            await edit("No requests.",
                       [[Button.inline(P.t("btn_back"), b"nav:account")]])
            return
        txt = "\n".join(f"• `{r['id']}` — {r['type']} — {r['status']}" for r in items)
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:account")]])
        return
    if data == "acc:referral":
        stats = REF.stats(rt.owner_id)
        await edit(P.t("your_referral", code=stats["code"]),
                   [[Button.inline(P.t("btn_back"), b"nav:account")]])
        return
    if data == "acc:notify":
        await edit(P.notify_text(), P.notify_buttons()); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]
        u = U.get_user(rt.owner_id) or {}
        n = (u.get("notify") or {})
        new_val = not bool(n.get(key, True))
        U.set_notify(rt.owner_id, key, new_val)
        await edit(P.notify_text(), P.notify_buttons()); return
    if data == "acc:qrlogin":
        await _handle_qr_login(rt, event); return

    if data == "mem:view":
        from .store import memory_store
        txt = "```\n" + (str(memory_store.all())[:3500]) + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]])
        return
    if data == "mem:clear":
        from .store import memory_store
        memory_store.replace({})
        await edit("Memory cleared.",
                   [[Button.inline(P.t("btn_back"), b"nav:memory")]])
        return
    if data == "mem:clearstate":
        state["step"] = "idle"
        state["phone"] = None
        state["hash"] = None
        state["awaiting"] = {}
        state["pending_targets"] = {}
        state["_resend_tried"] = False
        state["job_draft"] = {}
        rt.save_state()
        await edit("State cleared.",
                   [[Button.inline(P.t("btn_back"), b"nav:memory")]])
        return
    if data == "mem:dump":
        from .store import memory_store, user_state_store
        payload = {"memory": memory_store.all(), "state": user_state_store.all()}
        txt = "```json\n" + str(payload)[:3500] + "\n```"
        await edit(txt, [[Button.inline(P.t("btn_back"), b"nav:memory")]])
        return


# ===========================================================================
# QR login handler
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
            "**🔐 QR Code Login**\n\n"
            "1. Open **Telegram** on your phone (official app).\n"
            "2. Go to **Settings → Devices → Link Desktop Device**.\n"
            "3. Scan this QR code.\n\n"
            f"⏱ Expires in {remaining} seconds."
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