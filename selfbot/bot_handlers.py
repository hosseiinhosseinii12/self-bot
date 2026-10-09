"""Telegram bot runtime — everyone has full access.

Per-user panels, per-user sessions, per-user clocks.
Target group set via /setgroup <chat_id>.
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
from . import referrals as REF
from . import users as U
from . import clock as CLK
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


class BotRuntime:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_clients: dict = {}
        self.pairing_code: str = ""
        self.owner_id: int = int(CONFIG.get("owner_id", 338266658))
        self.started: bool = False
        self.stop_event = asyncio.Event()
        self.watchdog_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._state: dict = {}

    def state_for(self, user_id: int) -> dict:
        return self._state.setdefault(int(user_id), {
            "step": "idle",
            "phone": None,
            "hash": None,
            "awaiting": {},
            "job_draft": {},
            "_resend_tried": False,
        })

    def save_state(self) -> None:
        user_state_store.replace(self._state)

    def load_state(self) -> None:
        st = user_state_store.all() or {}
        if isinstance(st, dict):
            self._state = {int(k): v for k, v in st.items() if str(k).isdigit()}


def build_bot_runtime() -> BotRuntime:
    rt = BotRuntime()
    rt.load_state()
    rt.pairing_code = "".join(random.choices(string.digits, k=6))
    return rt


def _guarded(runtime: BotRuntime):
    def deco(fn):
        async def wrapper(event, *a, **kw):
            uid = event.sender_id or 0
            if uid and U.is_banned(uid):
                return
            if uid and not user_limiter.allow(uid):
                try:
                    await event.respond("Rate limited. Please slow down.")
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


def _public_base_url() -> str:
    base = os.environ.get("PUBLIC_URL") or ""
    if base:
        return base.rstrip("/")
    dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if dom:
        return f"https://{dom}"
    return "http://localhost:8080"


def _register_handlers(rt: BotRuntime) -> None:
    bot = rt.bot_client
    if bot is None:
        return

    # ---- /start ----
    @bot.on(events.NewMessage(pattern=r"^/start$", incoming=True))
    @_guarded(rt)
    async def _start(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        await event.respond(P.main_panel_text(uid), buttons=P.main_panel_buttons())

    # ---- /help ----
    @bot.on(events.NewMessage(pattern=r"^/help$", incoming=True))
    @_guarded(rt)
    async def _help(event):
        await event.respond(P.help_text(), buttons=P.help_buttons())

    # ---- /login ----
    @bot.on(events.NewMessage(pattern=r"^/login$", incoming=True))
    @_guarded(rt)
    async def _login(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        authorized = False
        try:
            client = rt.user_clients.get(uid)
            if client is None:
                from .bootstrap import make_user_client_for_login
                client = await make_user_client_for_login(uid)
                rt.user_clients[uid] = client
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False
        if authorized:
            await event.respond("Already logged in.")
            return
        st = rt.state_for(uid)
        st["step"] = "phone"
        st["phone"] = None
        st["hash"] = None
        st["_resend_tried"] = False
        rt.save_state()
        await event.respond("📱 Send your phone number with country code (e.g. +989121234567).")

    # ---- /logout ----
    @bot.on(events.NewMessage(pattern=r"^/logout$", incoming=True))
    @_guarded(rt)
    async def _logout(event):
        uid = event.sender_id
        client = rt.user_clients.get(uid)
        try:
            if client:
                await client.log_out()
        except Exception as e:
            log_bot.warning(f"logout error: {e}")
        session_file = DB_PATH / f"user_{uid}.session"
        if session_file.exists():
            session_file.unlink()
        rt.user_clients.pop(uid, None)
        st = rt.state_for(uid)
        st["step"] = "idle"
        st["phone"] = None
        st["hash"] = None
        st["awaiting"] = {}
        rt.save_state()
        await event.respond("Logged out and session deleted.")

    # ---- /setgroup ----
    @bot.on(events.NewMessage(pattern=r"^/setgroup(?:\s+(-?\d+))?$", incoming=True))
    @_guarded(rt)
    async def _setgroup(event):
        uid = event.sender_id
        arg = event.pattern_match.group(1)
        if not arg:
            await event.respond(P.group_selection_text(),
                                buttons=P.group_selection_buttons())
            return
        try:
            chat_id = int(arg)
        except Exception:
            await event.respond("Invalid chat ID. It must be a number like `-1001234567890`.")
            return
        U.update_user_settings(uid, job_target=chat_id)
        await event.respond(
            f"✅ Target group set to `{chat_id}`.\n\n"
            f"You can now create jobs with **Jobs → New job**.",
            parse_mode="md",
        )

    # ---- /account ----
    @bot.on(events.NewMessage(pattern=r"^/account$", incoming=True))
    @_guarded(rt)
    async def _account(event):
        uid = event.sender_id
        await event.respond(P.account_text(uid), buttons=P.account_buttons())

    # ---- /refer ----
    @bot.on(events.NewMessage(pattern=r"^/refer(?:\s+(\S+))?$", incoming=True))
    @_guarded(rt)
    async def _refer(event):
        code = event.pattern_match.group(1)
        uid = event.sender_id
        if not code:
            stats = REF.stats(uid)
            await event.respond(f"**Your referral code**\n\n`{stats['code']}`")
            return
        ok, msg = REF.apply_referral(uid, code)
        if ok:
            await event.respond(f"Referral applied. +{E.referral_bonus()} 💎 to both sides.")
        else:
            await event.respond("Invalid or already-used referral code.")

    # ---- .rep ----
    @bot.on(events.NewMessage(pattern=r"^\.rep\s+(\d+)\s+(\d+)\s+(.+)$", incoming=True))
    @_guarded(rt)
    async def _rep(event):
        uid = event.sender_id
        if event.chat_id != uid:
            return
        interval = int(event.pattern_match.group(1))
        duration = int(event.pattern_match.group(2))
        text = event.pattern_match.group(3).strip()

        target = U.get_setting(uid, "job_target")
        if not target:
            await event.respond(
                "❌ No target group set. Use `/setgroup <chat_id>` first.",
                parse_mode="md",
            )
            return

        ok, res = J.create_job(uid, target, interval, duration, text)
        if not ok:
            if res == "insufficient":
                bal = E.get_balance(uid)
                await event.respond(f"❌ Insufficient diamonds. Need {E.cost_job()}, you have {bal}.")
            else:
                await event.respond(res)
            return
        job = res
        task = asyncio.create_task(J.run_job(rt.bot_client, job))
        J.register_task(job["id"], task)
        await event.respond(f"✅ Job `{job['id']}` started — every {interval}s for {duration} min.")

    # ---- .stop ----
    @bot.on(events.NewMessage(pattern=r"^\.stop$", incoming=True))
    @_guarded(rt)
    async def _stop(event):
        uid = event.sender_id
        if event.chat_id != uid:
            return
        n = J.stop_all(owner_id=uid)
        await event.respond(f"Stopped {n} job(s).")

    # ---- callbacks ----
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

    # ---- text router ----
    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    @_guarded(rt)
    async def _text(event):
        text = (event.raw_text or "").strip()
        if not text:
            return
        if text.startswith(("/", ".")):
            return

        uid = event.sender_id
        st = rt.state_for(uid)
        step = st.get("step") or "idle"
        awaiting = st.get("awaiting") or {}

        if awaiting.get("qr_2fa_password"):
            awaiting.pop("qr_2fa_password", None)
            rt.save_state()
            try:
                from .qr_login import finish_qr_2fa
                ok, msg = await finish_qr_2fa(rt, uid, text)
                if ok:
                    await event.respond("✅ Logged in. Clock started.")
                else:
                    await event.respond(f"❌ {msg}")
            except Exception as e:
                log_bot.error(f"qr 2fa failed: {e}")
                await event.respond(f"❌ 2FA failed: {e}")
            return

        if awaiting.get("job_interval_custom"):
            awaiting.pop("job_interval_custom", None)
            rt.save_state()
            try:
                n = int(text)
                if n < 60:
                    await event.respond("Minimum is 60 seconds.")
                    return
                draft = st.setdefault("job_draft", {})
                draft["interval"] = n
                rt.save_state()
                await event.respond(P.job_duration_text(n),
                                    buttons=P.job_duration_buttons(n),
                                    parse_mode="md")
            except Exception:
                await event.respond("Invalid number.")
            return

        if awaiting.get("job_duration_custom"):
            awaiting.pop("job_duration_custom", None)
            rt.save_state()
            try:
                n = int(text)
                if n < 1:
                    await event.respond("Minimum is 1 minute.")
                    return
                draft = st.setdefault("job_draft", {})
                draft["duration"] = n
                rt.save_state()
                await event.respond(P.job_text_prompt(),
                                    buttons=P.job_text_buttons(),
                                    parse_mode="md")
            except Exception:
                await event.respond("Invalid number.")
            return

        if awaiting.get("job_text"):
            awaiting.pop("job_text", None)
            rt.save_state()
            draft = st.setdefault("job_draft", {})
            draft["text"] = text
            rt.save_state()
            await event.respond(P.job_confirm_text(draft),
                                buttons=P.job_confirm_buttons(draft),
                                parse_mode="md")
            return

        if awaiting.get("job_text_file") and event.document:
            awaiting.pop("job_text_file", None)
            rt.save_state()
            try:
                data_bytes = await event.download_media(bytes)
                file_text = data_bytes.decode("utf-8", errors="ignore")[:4000]
                draft = st.setdefault("job_draft", {})
                draft["text"] = file_text
                rt.save_state()
                await event.respond(P.job_confirm_text(draft),
                                    buttons=P.job_confirm_buttons(draft),
                                    parse_mode="md")
            except Exception as e:
                await event.respond(f"❌ Could not read file: {e}")
            return

        if step == "phone":
            phone = re.sub(r"[^\d+]", "", text)
            if not phone:
                await event.respond("Send your phone number with country code.")
                return
            try:
                client = rt.user_clients.get(uid)
                if client is None:
                    from .bootstrap import make_user_client_for_login
                    client = await make_user_client_for_login(uid)
                    rt.user_clients[uid] = client

                sent = await client.send_code_request(phone)
                phone_code_hash = sent.phone_code_hash
                log_bot.info(f"send_code_request OK for user {uid} phone {phone}")

                if not st.get("_resend_tried"):
                    st["_resend_tried"] = True
                    try:
                        sms_result = await client(functions.auth.ResendCodeRequest(
                            phone_number=phone,
                            phone_code_hash=phone_code_hash,
                        ))
                        phone_code_hash = sms_result.phone_code_hash
                        log_bot.info(f"ResendCodeRequest OK for user {uid}")
                    except Exception as resend_err:
                        err_name = type(resend_err).__name__
                        if "SendCodeUnavailable" in err_name:
                            log_bot.info("SendCodeUnavailable — app code only")
                        else:
                            log_bot.warning(f"ResendCodeRequest failed: {err_name}: {resend_err}")

                st["step"] = "code"
                st["phone"] = phone
                st["hash"] = phone_code_hash
                rt.save_state()
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
                    client = rt.user_clients.get(uid)
                    if client:
                        await client.connect()
                except Exception:
                    pass
            except Exception as e:
                log_bot.error(f"send_code_request failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
            return

        if step == "code":
            code = re.sub(r"\D", "", text)
            await _tidy(event)
            client = rt.user_clients.get(uid)
            if client is None:
                await event.respond("Session expired. Use /login again.")
                return
            try:
                await client.sign_in(st["phone"], code, phone_code_hash=st["hash"])
            except SessionPasswordNeededError:
                st["step"] = "2fa"
                rt.save_state()
                await event.respond("🔐 Send your 2FA password.")
                return
            except PhoneCodeInvalidError:
                await event.respond("Wrong code. Try again.")
                return
            except PhoneCodeExpiredError:
                st["step"] = "idle"
                st["phone"] = None
                st["hash"] = None
                st["_resend_tried"] = False
                rt.save_state()
                await event.respond("Code expired. Use /login again.")
                return
            except ConnectionError:
                await event.respond("❌ Connection lost.")
                return
            except Exception as e:
                log_bot.error(f"sign_in failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
                return
            await _finish_login(rt, uid, event, st)
            return

        if step == "2fa":
            await _tidy(event)
            client = rt.user_clients.get(uid)
            if client is None:
                await event.respond("Session expired. Use /login again.")
                return
            try:
                await client.sign_in(password=text)
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
            await _finish_login(rt, uid, event, st)
            return

        if awaiting.get("interval"):
            awaiting.pop("interval", None)
            rt.save_state()
            try:
                n = int(text)
                if 1 <= n <= 60:
                    U.update_user_settings(uid, interval=n)
                    await event.respond(f"Interval set to {n} min.")
                else:
                    await event.respond("Must be 1–60.")
            except Exception:
                await event.respond("Invalid number.")
            return

        if awaiting.get("timezone"):
            awaiting.pop("timezone", None)
            rt.save_state()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(text)
                U.update_user_settings(uid, timezone=text)
                await event.respond(f"Timezone set to `{text}`.")
            except Exception:
                await event.respond("Invalid timezone.")
            return

        if awaiting.get("base_name"):
            awaiting.pop("base_name", None)
            rt.save_state()
            U.update_user_settings(uid, base_name=text)
            await event.respond("Base name updated.")
            return

        if awaiting.get("custom_name_font"):
            awaiting.pop("custom_name_font", None)
            rt.save_state()
            if len(text) < 26:
                await event.respond("Need at least 26 characters (A–Z).")
            else:
                U.update_user_settings(uid, custom_name_font=text[:26])
                await event.respond("Custom name font saved.")
            return

        if awaiting.get("custom_clock_font"):
            awaiting.pop("custom_clock_font", None)
            rt.save_state()
            if len(text) < 10:
                await event.respond("Need at least 10 characters (0–9).")
            else:
                U.update_user_settings(uid, custom_clock_font=text[:10])
                await event.respond("Custom clock font saved.")
            return

        user_key = f"user_reqdiamonds:{uid}"
        if awaiting.get(user_key):
            awaiting.pop(user_key, None)
            rt.save_state()
            try:
                amount = int(text)
                if not (1 <= amount <= 10000):
                    raise ValueError
            except Exception:
                await event.respond("Invalid amount. Send a number 1–10000.")
                return
            req = R.create_diamond_request(uid, amount)
            await event.respond(f"✅ Request `{req['id']}` sent to admin.")
            return


async def _finish_login(rt: BotRuntime, uid: int, event, st: dict) -> None:
    st["step"] = "idle"
    st["phone"] = None
    st["hash"] = None
    st["_resend_tried"] = False
    rt.save_state()
    try:
        client = rt.user_clients.get(uid)
        me = await client.get_me()
        log_bot.info(f"Logged in user {uid} as {me.first_name} ({me.id})")
    except Exception:
        pass
    await event.respond("✅ Logged in.")
    try:
        client = rt.user_clients.get(uid)
        settings = U.get_user_settings(uid)
        if settings.get("clock_on") and client:
            CLK.start_clock(client, uid)
    except Exception:
        pass


async def _route_callback(rt: BotRuntime, event, data: str) -> None:
    bot = rt.bot_client
    uid = event.sender_id
    st = rt.state_for(uid)
    settings = U.get_user_settings(uid)

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

    if data == "nav:main":
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return
    if data == "nav:status":
        await edit(P.status_text(uid), P.status_buttons()); return
    if data == "nav:settings":
        await edit(P.settings_text(uid), P.settings_buttons()); return
    if data == "nav:appearance":
        await edit(P.appearance_text(uid), P.appearance_buttons()); return
    if data == "nav:jobs":
        await edit(P.jobs_text(uid), P.jobs_buttons()); return
    if data == "nav:account":
        await edit(P.account_text(uid), P.account_buttons()); return
    if data == "nav:help":
        await edit(P.help_text(), P.help_buttons()); return

    if data == "nav:webpanel":
        try:
            from .user_panel import issue_web_token
            tok = issue_web_token(uid)
            url = f"{_public_base_url()}/user/?token={tok}"
            await edit(
                "**🌐 Your Web Panel**\n\n"
                "Open the panel below to manage your account, "
                "request diamonds, and view your history.\n\n"
                "_Keep the link private._",
                [
                    [Button.url("Open Web Panel", url)],
                    [Button.inline("◀️ Back", b"nav:main")],
                ],
            )
        except Exception as e:
            log_bot.error(f"webpanel error: {e}")
            await edit(f"❌ Could not generate link: {e}",
                       [[Button.inline("◀️ Back", b"nav:main")]])
        return

    if data == "clock:on":
        client = rt.user_clients.get(uid)
        if not client:
            await _safe_answer(event, "Login first (/login).", alert=True)
            return
        try:
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False
        if not authorized:
            await _safe_answer(event, "Login first (/login).", alert=True)
            return
        U.update_user_settings(uid, clock_on=True)
        CLK.start_clock(client, uid)
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return

    if data == "clock:off":
        client = rt.user_clients.get(uid)
        if client:
            await CLK.stop_clock_async(client, uid)
        else:
            CLK.stop_clock(uid)
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return

    if data == "set:interval":
        st["awaiting"]["interval"] = True
        rt.save_state()
        await edit("Send interval (1–60):",
                   [[Button.inline("◀️ Back", b"nav:settings")]])
        return
    if data == "set:timezone":
        st["awaiting"]["timezone"] = True
        rt.save_state()
        await edit("Send timezone (e.g. Europe/Berlin):",
                   [[Button.inline("◀️ Back", b"nav:settings")]])
        return
    if data == "set:language":
        await edit(P.language_text(), P.language_buttons()); return
    if data.startswith("set:lang:"):
        lang = data.split(":")[-1]
        CONFIG["language"] = lang
        save_config(CONFIG)
        await edit(P.language_text(), P.language_buttons()); return

    if data == "app:base":
        st["awaiting"]["base_name"] = True
        rt.save_state()
        await edit("Send new base name:",
                   [[Button.inline("◀️ Back", b"nav:appearance")]])
        return
    if data == "app:namefont":
        rows = [[Button.inline(n, f"app:namefont:{n}".encode())] for n in
                ["normal", "italic", "bold", "script", "fraktur", "double", "sans", "sans-bold"]]
        rows.append([Button.inline("◀️ Back", b"nav:appearance")])
        await edit("Pick a name font:", rows)
        return
    if data.startswith("app:namefont:"):
        font = data.split(":")[-1]
        U.update_user_settings(uid, name_font=font)
        await edit(P.appearance_text(uid), P.appearance_buttons()); return
    if data == "app:clockfont":
        from .fonts import DIGIT_FONTS
        rows = [[Button.inline(n, f"app:clockfont:{n}".encode())] for n in DIGIT_FONTS.keys()]
        rows.append([Button.inline("◀️ Back", b"nav:appearance")])
        await edit("Pick a clock font:", rows)
        return
    if data.startswith("app:clockfont:"):
        font = data.split(":")[-1]
        U.update_user_settings(uid, clock_font=font)
        await edit(P.appearance_text(uid), P.appearance_buttons()); return
    if data == "app:customname":
        st["awaiting"]["custom_name_font"] = True
        rt.save_state()
        await edit("Send 26 characters for A–Z:",
                   [[Button.inline("◀️ Back", b"nav:appearance")]])
        return
    if data == "app:customclock":
        st["awaiting"]["custom_clock_font"] = True
        rt.save_state()
        await edit("Send 10 characters for 0–9:",
                   [[Button.inline("◀️ Back", b"nav:appearance")]])
        return

    if data == "job:new":
        st["job_draft"] = {}
        st["awaiting"] = {}
        rt.save_state()
        target = settings.get("job_target")
        if not target:
            await edit(P.group_selection_text(), P.group_selection_buttons())
            return
        await edit(P.job_setup_text(), P.job_setup_buttons())
        return

    if data == "job:groupid:help":
        await edit(P.group_id_help_text(),
                   [[Button.inline("◀️ Back", b"nav:jobs")]])
        return

    if data.startswith("job:interval:"):
        val = data.split(":")[2]
        if val == "custom":
            st["awaiting"]["job_interval_custom"] = True
            rt.save_state()
            await edit("**Custom interval**\n\nSend a number (seconds, min 60):",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
            return
        try:
            interval_min = int(val)
        except ValueError:
            return
        st.setdefault("job_draft", {})["interval"] = interval_min * 60
        rt.save_state()
        await edit(P.job_duration_text(interval_min),
                   P.job_duration_buttons(interval_min))
        return

    if data.startswith("job:duration:"):
        val = data.split(":")[2]
        if val == "custom":
            st["awaiting"]["job_duration_custom"] = True
            rt.save_state()
            await edit("**Custom duration**\n\nSend a number (minutes):",
                       [[Button.inline("◀️ Back", b"job:setup:back")]])
            return
        try:
            duration = int(val)
        except ValueError:
            return
        st.setdefault("job_draft", {})["duration"] = duration
        rt.save_state()
        await edit(P.job_text_prompt(), P.job_text_buttons())
        return

    if data == "job:text:enter":
        st["awaiting"]["job_text"] = True
        rt.save_state()
        await edit("Send the message text now:",
                   [[Button.inline("◀️ Back", b"job:setup:back")]])
        return
    if data == "job:text:upload":
        st["awaiting"]["job_text_file"] = True
        rt.save_state()
        await edit("Send a `.txt` file with the message:",
                   [[Button.inline("◀️ Back", b"job:setup:back")]])
        return

    if data == "job:confirm:start":
        draft = st.get("job_draft", {})
        interval = draft.get("interval", 0)
        duration = draft.get("duration", 0)
        text = draft.get("text", "")
        target = settings.get("job_target")
        if not target:
            await edit("❌ No target group. Use `/setgroup <chat_id>` first.",
                       [[Button.inline("◀️ Back", b"nav:jobs")]])
            return
        ok, res = J.create_job(uid, target, interval, duration, text)
        if ok:
            job = res
            task = asyncio.create_task(J.run_job(rt.bot_client, job))
            J.register_task(job["id"], task)
            st["job_draft"] = {}
            rt.save_state()
            await edit(f"✅ Job `{job['id']}` started.\n"
                       f"Every {interval}s for {duration} min.",
                       [[Button.inline("📋 Jobs", b"nav:jobs")]])
        else:
            if res == "insufficient":
                bal = E.get_balance(uid)
                await edit(f"❌ Insufficient diamonds. Need {E.cost_job()}, you have {bal}.",
                           [[Button.inline("◀️ Back", b"job:setup:back")]])
            else:
                await edit(f"❌ {res}",
                           [[Button.inline("◀️ Back", b"job:setup:back")]])
        return

    if data == "job:setup:back":
        st["job_draft"] = {}
        for k in ("job_interval_custom", "job_duration_custom",
                  "job_text", "job_text_file"):
            st["awaiting"].pop(k, None)
        rt.save_state()
        await edit(P.job_setup_text(), P.job_setup_buttons())
        return

    if data == "job:list":
        await edit(P.jobs_text(uid), P.jobs_buttons()); return
    if data == "job:stopall":
        n = J.stop_all(owner_id=uid)
        await edit(f"Stopped {n} jobs.",
                   [[Button.inline("◀️ Back", b"nav:jobs")]])
        return
    if data == "job:templates":
        tpls = J.list_templates(uid)
        if not tpls:
            await edit("No templates.",
                       [[Button.inline("◀️ Back", b"nav:jobs")]])
            return
        rows = [[Button.inline(n, f"job:tpl:{n}".encode())] for n in tpls.keys()]
        rows.append([Button.inline("◀️ Back", b"nav:jobs")])
        await edit("Templates:", rows)
        return
    if data.startswith("job:tpl:"):
        name = data.split(":", 2)[-1]
        tpl = J.load_template(uid, name) or ""
        await edit(f"**{name}**\n\n{tpl}",
                   [[Button.inline("◀️ Back", b"nav:jobs")]])
        return

    if data == "acc:reqdiamonds":
        st["awaiting"][f"user_reqdiamonds:{uid}"] = True
        rt.save_state()
        await edit("💎 **Request diamonds**\n\nSend the amount (1–10000):",
                   [[Button.inline("◀️ Back", b"nav:account")]])
        return
    if data == "acc:myreq":
        items = R.for_user(uid)[:20]
        if not items:
            await edit("No requests.", [[Button.inline("◀️ Back", b"nav:account")]])
            return
        txt = "\n".join(f"• `{r['id']}` — {r['type']} — {r['status']}" for r in items)
        await edit(txt, [[Button.inline("◀️ Back", b"nav:account")]])
        return
    if data == "acc:referral":
        stats = REF.stats(uid)
        await edit(
            f"**🤝 My referral code**\n\n`{stats['code']}`\n\n"
            f"Share this code — you both get diamonds when a friend joins.\n\n"
            f"**Total referrals:** {stats['count']}",
            [[Button.inline("◀️ Back", b"nav:account")]],
        )
        return
    if data == "acc:notify":
        await edit(P.notify_text(), P.notify_buttons()); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]
        u = U.get_user(uid) or {}
        n = (u.get("notify") or {})
        new_val = not bool(n.get(key, True))
        U.set_notify(uid, key, new_val)
        await edit(P.notify_text(), P.notify_buttons()); return
    if data == "acc:qrlogin":
        await _handle_qr_login(rt, uid, event); return


async def _handle_qr_login(rt: BotRuntime, uid: int, event) -> None:
    bot = rt.bot_client

    try:
        from .qr_login import diagnose
        d = diagnose()
        if not d["telethon"]:
            await bot.send_message(event.chat_id,
                                   f"❌ QR Login unavailable.\nTelethon error: `{d['telethon_error']}`")
            return
        if not d["qrcode"]:
            await bot.send_message(event.chat_id,
                                   f"❌ QR Login unavailable.\nqrcode error: `{d['qrcode_error']}`")
            return
    except Exception as e:
        log_bot.error(f"qr diagnose failed: {type(e).__name__}: {e}")

    try:
        ok, msg, png, expires_at = await QRL.start_qr_login(rt, uid)
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
        await bot.send_file(event.chat_id, io.BytesIO(png),
                            caption=caption, parse_mode="md", force_document=False)
    except Exception as e:
        log_bot.error(f"QR image send failed: {type(e).__name__}: {e}")
        try:
            await bot.send_message(event.chat_id, f"Failed to send QR image: {e}")
        except Exception:
            pass


async def _watchdog(rt: BotRuntime) -> None:
    while not rt.stop_event.is_set():
        try:
            if rt.bot_client and not rt.bot_client.is_connected():
                await rt.bot_client.connect()
            for uid, client in list(rt.user_clients.items()):
                if client and not client.is_connected():
                    try:
                        await client.connect()
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