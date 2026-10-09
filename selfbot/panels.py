"""Inline keyboard panels for the Telegram bot (English + Persian).

Two panels:
  * Owner panel: full control (clock, jobs, settings, memory, ...)
  * User panel:  for regular users (web panel, account, requests, referral)

Button styles are minimal — most buttons stay default to match the
clean monochrome admin theme.
"""
import random
from typing import List, Optional

from .config import CONFIG
from .i18n import t
from .subscriptions import days_left, effective_plan
from .users import get_user

try:
    from telethon import Button
except ImportError:
    Button = None  # type: ignore


# ===========================================================================
# Owner: Main panel
# ===========================================================================
def main_panel_buttons() -> List[List]:
    return [
        [Button.inline(t("btn_on"), b"clock:on", style="success"),
         Button.inline(t("btn_off"), b"clock:off", style="danger")],
        [Button.inline(t("btn_status"), b"nav:status", style="primary")],
        [Button.inline(t("btn_settings"), b"nav:settings", style="primary"),
         Button.inline(t("btn_appearance"), b"nav:appearance", style="primary")],
        [Button.inline(t("btn_jobs"), b"nav:jobs", style="primary"),
         Button.inline(t("btn_account"), b"nav:account", style="primary")],
        [Button.inline(t("btn_memory"), b"nav:memory", style="primary"),
         Button.inline(t("btn_help"), b"nav:help", style="primary")],
    ]


def main_panel_text() -> str:
    from .clock import last_written
    from .economy import cost_clock, cost_job, get_balance

    owner = int(CONFIG.get("owner_id", 338266658))
    state = "🟢 ON" if CONFIG.get("clock_on") else "🔴 OFF"
    base = CONFIG.get("base_name", "User")
    interval = CONFIG.get("interval", 5)
    tz = CONFIG.get("timezone", "UTC")
    last = last_written() or "—"
    plan = effective_plan(owner)
    bal = get_balance(owner)

    if plan in ("free", "basic"):
        costs = (
            f"Clock update: {cost_clock()} 💎  •  "
            f"New job: {cost_job()} 💎  •  "
            f"Job message: 1 💎"
        )
    else:
        costs = f"Plan `{plan}` — no diamond cost per update."

    return (
        f"**{t('main_panel')}**\n\n"
        f"Clock: {state}\n"
        f"{t('interval')}: {interval} min\n"
        f"{t('timezone')}: {tz}\n"
        f"{t('base_name')}: `{base}`\n"
        f"Last name: `{last}`\n\n"
        f"💎 Balance: **{bal}**  •  Plan: **{plan}**\n"
        f"{costs}"
    )


# ===========================================================================
# Owner: Status
# ===========================================================================
def status_buttons() -> List[List]:
    return [[Button.inline(t("btn_back"), b"nav:main")]]


def status_text() -> str:
    from .clock import last_written
    state = t("status_on") if CONFIG.get("clock_on") else t("status_off")
    return (
        f"**{t('btn_status')}**\n\n"
        f"{state}\n"
        f"{t('interval')}: {CONFIG.get('interval', 5)} min\n"
        f"{t('timezone')}: {CONFIG.get('timezone', 'UTC')}\n"
        f"{t('base_name')}: `{CONFIG.get('base_name', 'User')}`\n"
        f"{t('name_font')}: {CONFIG.get('name_font', 'normal')}\n"
        f"{t('clock_font')}: {CONFIG.get('clock_font', 'double')}\n"
        f"Last written: `{last_written() or '—'}`"
    )


# ===========================================================================
# Owner: Settings
# ===========================================================================
def settings_buttons() -> List[List]:
    return [
        [Button.inline("⏱ " + t("interval"), b"set:interval", style="primary"),
         Button.inline("🌍 " + t("timezone"), b"set:timezone", style="primary")],
        [Button.inline("🗣 " + t("language"), b"set:language", style="primary")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def settings_text() -> str:
    return (
        f"**{t('btn_settings')}**\n\n"
        f"{t('interval')}: {CONFIG.get('interval', 5)} min\n"
        f"{t('timezone')}: {CONFIG.get('timezone', 'UTC')}\n"
        f"{t('language')}: {CONFIG.get('language', 'en')}"
    )


# ===========================================================================
# Owner: Appearance
# ===========================================================================
def appearance_buttons() -> List[List]:
    return [
        [Button.inline("✏️ " + t("base_name"), b"app:base", style="primary")],
        [Button.inline("🔤 " + t("name_font"), b"app:namefont", style="primary"),
         Button.inline("🕐 " + t("clock_font"), b"app:clockfont", style="primary")],
        [Button.inline("🅰️ Custom name font", b"app:customname", style="primary"),
         Button.inline("0️⃣ Custom clock font", b"app:customclock", style="primary")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def appearance_text() -> str:
    return (
        f"**{t('btn_appearance')}**\n\n"
        f"{t('base_name')}: `{CONFIG.get('base_name', 'User')}`\n"
        f"{t('name_font')}: {CONFIG.get('name_font', 'normal')}\n"
        f"{t('clock_font')}: {CONFIG.get('clock_font', 'double')}\n"
        f"Custom name font: {'set' if CONFIG.get('custom_name_font') else '—'}\n"
        f"Custom clock font: {'set' if CONFIG.get('custom_clock_font') else '—'}"
    )


# ===========================================================================
# Owner: Jobs list
# ===========================================================================
def jobs_buttons() -> List[List]:
    from .jobs import active_count
    from .subscriptions import plan_limits
    owner = int(CONFIG.get("owner_id", 338266658))
    lim = plan_limits(owner)
    return [
        [Button.inline(f"➕ New job ({active_count()}/{lim['max_jobs']})",
                       b"job:new", style="success")],
        [Button.inline("📋 " + t("btn_jobs"), b"job:list", style="primary"),
         Button.inline("⏹ Stop all", b"job:stopall", style="danger")],
        [Button.inline("📁 Templates", b"job:templates", style="primary")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def jobs_text() -> str:
    from .jobs import active_jobs
    from .subscriptions import plan_limits
    from .economy import cost_job

    owner = int(CONFIG.get("owner_id", 338266658))
    lim = plan_limits(owner)
    lines = [f"**{t('btn_jobs')}**", ""]
    jobs = active_jobs()
    if not jobs:
        lines.append(t("no_jobs"))
    else:
        for j in jobs:
            lines.append(
                f"• `{j['id']}` every {j['interval']}s for {j['duration']}m — {j['sent']} sent"
            )
    lines.append("")
    lines.append(f"Cost per new job: **{cost_job()} 💎**")
    lines.append("")
    lines.append(
        f"Plan: {effective_plan(owner)} — max jobs {lim['max_jobs']}, "
        f"min interval {lim['min_interval']}s, max duration {lim['max_duration']}m"
    )
    return "\n".join(lines)


# ===========================================================================
# Owner: Job creation flow
# ===========================================================================
def job_setup_buttons() -> List[List]:
    return [
        [Button.inline("⚡ 1 min", b"job:interval:1", style="danger"),
         Button.inline("🕐 5 min", b"job:interval:5", style="primary")],
        [Button.inline("⏰ 15 min", b"job:interval:15", style="primary"),
         Button.inline("🕓 30 min", b"job:interval:30", style="primary")],
        [Button.inline("🕕 60 min", b"job:interval:60", style="primary")],
        [Button.inline("✏️ Custom interval", b"job:interval:custom", style="success")],
        [Button.inline("◀️ Back", b"nav:jobs")],
    ]


def job_setup_text() -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 1/3 — Interval**\n\n"
        "How often should the message be sent?\n\n"
        "Choose a preset or tap **Custom** to enter your own value."
    )


def job_duration_buttons(interval: int) -> List[List]:
    return [
        [Button.inline("⏱ 1 min", b"job:duration:1", style="primary"),
         Button.inline("⏱ 5 min", b"job:duration:5", style="primary")],
        [Button.inline("⏱ 30 min", b"job:duration:30", style="primary"),
         Button.inline("⏱ 1 hour", b"job:duration:60", style="primary")],
        [Button.inline("⏱ 3 hours", b"job:duration:180", style="primary"),
         Button.inline("⏱ 6 hours", b"job:duration:360", style="primary")],
        [Button.inline("⏱ 12 hours", b"job:duration:720", style="primary")],
        [Button.inline("✏️ Custom duration", b"job:duration:custom", style="success")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def job_duration_text(interval: int) -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        f"**Step 2/3 — Duration**\n\n"
        f"Interval set to **{interval} min**.\n\n"
        "How long should the job run?\n\n"
        "Choose a preset or tap **Custom**."
    )


def job_text_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Enter text", b"job:text:enter", style="primary")],
        [Button.inline("📎 Upload .txt", b"job:text:upload", style="success")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def job_text_prompt() -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 3/3 — Message**\n\n"
        "Send the text you want to repeat.\n\n"
        "**Variables available:**\n"
        "`{time}` `{date}` `{job_id}` `{sent}` `{name}`\n\n"
        "Tap **Enter text** or **Upload .txt**."
    )


def job_confirm_buttons(job: dict) -> List[List]:
    from .subscriptions import plan_limits
    owner = int(CONFIG.get("owner_id", 338266658))
    lim = plan_limits(owner)

    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")

    can_start = (
        target is not None
        and interval >= lim["min_interval"]
        and duration <= lim["max_duration"]
        and text
    )

    rows = []
    if can_start:
        rows.append([Button.inline("✅ Start job", b"job:confirm:start", style="success")])
    else:
        rows.append([Button.inline("⚠️ Cannot start", b"job:confirm:blocked", style="danger")])
    rows.append([Button.inline("◀️ Back", b"job:setup:back")])
    rows.append([Button.inline("❌ Cancel", b"nav:jobs")])
    return rows


def job_confirm_text(job: dict) -> str:
    from .subscriptions import plan_limits, effective_plan
    from .economy import cost_job

    owner = int(CONFIG.get("owner_id", 338266658))
    lim = plan_limits(owner)
    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")

    warnings = []
    if target is None:
        warnings.append("❌ No target group selected")
    if interval < lim["min_interval"]:
        warnings.append(f"❌ Min interval for `{effective_plan(owner)}` is **{lim['min_interval']}s**")
    if duration > lim["max_duration"]:
        warnings.append(f"❌ Max duration is **{lim['max_duration']} min**")
    if not text:
        warnings.append("❌ No message text set")

    status = "\n".join(warnings) if warnings else "✅ Ready to start"

    return (
        "**🔁 Job Summary**\n\n"
        f"**Target:** `{target if target else '— not selected —'}`\n"
        f"**Interval:** `{interval}` min\n"
        f"**Duration:** `{duration}` min\n"
        f"**Messages:** ~`{duration * 60 // max(interval, 1)}`\n"
        f"**Cost:** `{cost_job()}` 💎\n\n"
        f"**Message:**\n`{text[:200] or '(empty)'}`\n\n"
        f"{status}"
    )


# ===========================================================================
# Owner: Target group selection
# ===========================================================================
def group_selection_reply_buttons():
    try:
        from telethon.tl.types import (
            KeyboardButtonRequestPeer,
            RequestPeerTypeChat,
            KeyboardButtonRow,
            ReplyKeyboardMarkup,
        )
    except ImportError:
        return None

    return ReplyKeyboardMarkup(
        rows=[
            KeyboardButtonRow(
                buttons=[
                    KeyboardButtonRequestPeer(
                        text="🎯 Select a group",
                        button_id=random.randint(100000, 999999),
                        peer_type=RequestPeerTypeChat(),
                        max_quantity=1,
                    )
                ]
            )
        ],
        resize=True,
        single_use=True,
        placeholder="Tap 🎯 to choose a group…",
    )


def group_selection_text() -> str:
    return (
        "**🎯 Select Target Group**\n\n"
        "Tap the **🎯 Select a group** button below your keyboard.\n\n"
        "Telegram will open your chat list — pick the group you want."
    )


# ===========================================================================
# Owner: Account
# ===========================================================================
def account_buttons() -> List[List]:
    return [
        [Button.inline("💎 " + t("request_diamonds"), b"acc:reqdiamonds", style="primary")],
        [Button.inline("⭐ " + t("request_subscription"), b"acc:reqsub", style="primary")],
        [Button.inline("📜 " + t("my_requests"), b"acc:myreq", style="primary")],
        [Button.inline("🤝 " + t("your_referral").split(":")[0], b"acc:referral", style="primary")],
        [Button.inline("🔔 " + t("notify_prefs"), b"acc:notify", style="primary")],
        [Button.inline("🔐 QR Login", b"acc:qrlogin", style="success")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def account_text() -> str:
    from .economy import cost_clock, cost_job, get_balance

    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    plan = effective_plan(owner)
    days = days_left(owner)
    bal = get_balance(owner)
    return (
        f"**{t('btn_account')}**\n\n"
        f"{t('diamonds')}: **{bal}** 💎\n"
        f"{t('plan')}: {plan}\n"
        f"{t('days_left')}: {days}\n\n"
        f"Costs: clock update {cost_clock()} 💎, new job {cost_job()} 💎\n\n"
        f"{t('your_referral', code=u.get('referral_code', '—'))}"
    )


# ===========================================================================
# Owner: Memory
# ===========================================================================
def memory_buttons() -> List[List]:
    return [
        [Button.inline("🧠 View memory", b"mem:view", style="primary")],
        [Button.inline("🗑 Clear memory", b"mem:clear", style="danger"),
         Button.inline("♻️ Clear my state", b"mem:clearstate", style="danger")],
        [Button.inline("📤 Dump JSON", b"mem:dump", style="primary")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def memory_text() -> str:
    from .store import memory_store, user_state_store
    mem = memory_store.all()
    st = user_state_store.all()
    return (
        f"**{t('btn_memory')}**\n\n"
        f"Memory keys: {len(mem) if isinstance(mem, dict) else 0}\n"
        f"User state keys: {len(st) if isinstance(st, dict) else 0}"
    )


# ===========================================================================
# Owner: Help
# ===========================================================================
def help_buttons() -> List[List]:
    return [[Button.inline(t("btn_back"), b"nav:main")]]


def help_text() -> str:
    return (
        "**Help**\n\n"
        "Commands:\n"
        "/start — main panel\n"
        "/login — log in with phone → code\n"
        "/logout — delete session\n"
        "/account — diamonds + subscription + QR Login\n"
        "/request — request diamonds or subscription\n"
        "/myrequests — list your requests\n"
        "/refer <code> — apply referral code\n"
        "/name <base> — set base name\n"
        "/font <name> — set name font\n"
        "/clockfont <name> — set digit font\n"
        "/tz <zone> — set timezone\n"
        "/interval <n> — set interval (1–60 min)\n"
        "/on — enable clock\n"
        "/off — disable clock\n"
        "/status — show current state\n"
        "/memory — memory tools\n\n"
        "QR Login:\n"
        "Account → 🔐 QR Login — scan with the official Telegram app\n"
        "(no SMS needed)\n\n"
        "Repeat jobs (owner only, in bot DM):\n"
        "`.rep <seconds> <minutes> <text>` — start a repeat job\n"
        "`.stop` — stop jobs in this chat"
    )


# ===========================================================================
# Owner: Language picker
# ===========================================================================
def language_buttons() -> List[List]:
    return [
        [Button.inline("English", b"set:lang:en", style="primary"),
         Button.inline("فارسی", b"set:lang:fa", style="primary")],
        [Button.inline(t("btn_back"), b"nav:settings")],
    ]


def language_text() -> str:
    return (
        "**Language / زبان**\n\n"
        f"Current: {CONFIG.get('language', 'en')}"
    )


# ===========================================================================
# Owner: Notification preferences
# ===========================================================================
def notify_buttons() -> List[List]:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    notify = u.get("notify") or {}

    def mark(k):
        return "✅" if notify.get(k, True) else "❌"

    return [
        [Button.inline(f"{mark('job_completion')} {t('notify_job')}",
                       b"notify:job_completion", style="primary")],
        [Button.inline(f"{mark('diamond_changes')} {t('notify_diamond')}",
                       b"notify:diamond_changes", style="primary")],
        [Button.inline(f"{mark('subscription_expiry')} {t('notify_expiry')}",
                       b"notify:subscription_expiry", style="primary")],
        [Button.inline(t("btn_back"), b"nav:account")],
    ]


def notify_text() -> str:
    return f"**{t('notify_prefs')}**"


# ===========================================================================
# Owner: Plan picker
# ===========================================================================
def plan_buttons() -> List[List]:
    return [
        [Button.inline("Basic — 30 days", b"reqsub:basic", style="primary")],
        [Button.inline("Pro — 30 days", b"reqsub:pro", style="primary")],
        [Button.inline("VIP — 30 days", b"reqsub:vip", style="primary")],
        [Button.inline(t("btn_back"), b"nav:account")],
    ]


def plan_text() -> str:
    return (
        f"**{t('request_subscription')}**\n\n"
        "Pick a plan. An admin will review your request."
    )


# ===========================================================================
# USER (non-owner) panel
# ===========================================================================
def user_panel_buttons(user_id: int) -> List[List]:
    """Clean, minimal user panel. No loud colors — all default style."""
    return [
        [Button.inline("🌐 My Web Panel", b"user:webpanel")],
        [Button.inline("💎 My Account", b"user:account"),
         Button.inline("📋 Help", b"user:help")],
        [Button.inline("💎 Request diamonds", b"user:reqdiamonds"),
         Button.inline("⭐ Request subscription", b"user:reqsub")],
        [Button.inline("🤝 My referral", b"user:referral"),
         Button.inline("📜 My requests", b"user:myreq")],
    ]


def user_panel_text(user_id: int) -> str:
    from .economy import get_balance
    from .users import get_user

    u = get_user(user_id) or {}
    plan = effective_plan(user_id)
    days = days_left(user_id)
    bal = get_balance(user_id)
    name = u.get("first_name") or "there"

    return (
        f"👋 **Hello {name}!**\n\n"
        f"Welcome to **SELF BOT** — a Telegram profile clock service.\n\n"
        f"💎 **Your balance:** {bal} diamonds\n"
        f"📅 **Your plan:** {plan}\n"
        f"⏳ **Days left:** {days}\n\n"
        f"Use the buttons below to manage your account."
    )


def user_account_buttons() -> List[List]:
    return [
        [Button.inline("💎 Request diamonds", b"user:reqdiamonds")],
        [Button.inline("⭐ Request subscription", b"user:reqsub")],
        [Button.inline("📜 My requests", b"user:myreq")],
        [Button.inline("◀️ Back", b"user:home")],
    ]


def user_account_text(user_id: int) -> str:
    from .economy import get_balance
    from .users import get_user

    u = get_user(user_id) or {}
    plan = effective_plan(user_id)
    days = days_left(user_id)
    bal = get_balance(user_id)
    referral_code = u.get("referral_code", "—")

    return (
        f"**👤 My Account**\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"💎 Diamonds: **{bal}**\n"
        f"📅 Plan: **{plan}**\n"
        f"⏳ Days left: **{days}**\n"
        f"🤝 Referral code: `{referral_code}`\n\n"
        f"Use the buttons below to submit a request."
    )


def user_help_buttons() -> List[List]:
    return [[Button.inline("◀️ Back", b"user:home")]]


def user_help_text() -> str:
    return (
        "**📋 Help**\n\n"
        "This bot updates your Telegram profile name with the current time.\n\n"
        "**Available actions:**\n"
        "• 🌐 My Web Panel — open your account on the web\n"
        "• 💎 Request diamonds — ask the admin for diamonds\n"
        "• ⭐ Request subscription — ask for a paid plan\n"
        "• 📜 My requests — see your request history\n"
        "• 🤝 My referral — share your referral code\n\n"
        "**Plans:**\n"
        "• Free — 1 job, min interval 300s\n"
        "• Basic — 3 jobs, min interval 120s\n"
        "• Pro — 10 jobs, min interval 60s\n"
        "• VIP — 50 jobs, min interval 30s\n\n"
        "For more info, contact the admin."
    )