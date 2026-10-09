"""Inline keyboard panels for the Telegram bot.

Everyone gets the full control panel now.
No tiers, no plans — the economy is pure diamonds.
"""
from typing import List, Optional

from .config import CONFIG
from .users import get_user, get_user_settings

try:
    from telethon import Button
except ImportError:
    Button = None  # type: ignore


# ===========================================================================
# Main panel
# ===========================================================================
def main_panel_buttons() -> List[List]:
    return [
        [Button.inline("🟢 Clock ON", b"clock:on", style="success"),
         Button.inline("🔴 Clock OFF", b"clock:off", style="danger")],
        [Button.inline("📊 Status", b"nav:status")],
        [Button.inline("⚙️ Settings", b"nav:settings"),
         Button.inline("🎨 Appearance", b"nav:appearance")],
        [Button.inline("🔁 Jobs", b"nav:jobs"),
         Button.inline("💎 Account", b"nav:account")],
        [Button.inline("🌐 Web Panel", b"nav:webpanel"),
         Button.inline("❓ Help", b"nav:help")],
    ]


def main_panel_text(user_id: int = None) -> str:
    from .clock import last_written
    from .economy import cost_clock, cost_job, get_balance

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    state = "🟢 ON" if settings.get("clock_on") else "🔴 OFF"
    base = settings.get("base_name", "User")
    interval = settings.get("interval", 5)
    tz = settings.get("timezone", "UTC")
    last = last_written(uid) or "—"
    bal = get_balance(uid)

    return (
        f"**Control Panel**\n\n"
        f"Clock: {state}\n"
        f"Interval: {interval} min\n"
        f"Timezone: {tz}\n"
        f"Base name: `{base}`\n"
        f"Last name: `{last}`\n\n"
        f"💎 Balance: **{bal}**\n"
        f"Costs: update {cost_clock()} 💎 · new job {cost_job()} 💎"
    )


# ===========================================================================
# Status
# ===========================================================================
def status_buttons() -> List[List]:
    return [[Button.inline("◀️ Back", b"nav:main")]]


def status_text(user_id: int = None) -> str:
    from .clock import last_written
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    state = "ON" if settings.get("clock_on") else "OFF"
    return (
        f"**Status**\n\n"
        f"Clock: {state}\n"
        f"Interval: {settings.get('interval', 5)} min\n"
        f"Timezone: {settings.get('timezone', 'UTC')}\n"
        f"Base name: `{settings.get('base_name', 'User')}`\n"
        f"Name font: {settings.get('name_font', 'normal')}\n"
        f"Clock font: {settings.get('clock_font', 'double')}\n"
        f"Last written: `{last_written(uid) or '—'}`"
    )


# ===========================================================================
# Settings
# ===========================================================================
def settings_buttons() -> List[List]:
    return [
        [Button.inline("⏱ Interval", b"set:interval"),
         Button.inline("🌍 Timezone", b"set:timezone")],
        [Button.inline("🗣 Language", b"set:language")],
        [Button.inline("◀️ Back", b"nav:main")],
    ]


def settings_text(user_id: int = None) -> str:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    return (
        f"**Settings**\n\n"
        f"Interval: {settings.get('interval', 5)} min\n"
        f"Timezone: {settings.get('timezone', 'UTC')}\n"
        f"Language: {CONFIG.get('language', 'en')}"
    )


# ===========================================================================
# Appearance
# ===========================================================================
def appearance_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Base name", b"app:base")],
        [Button.inline("🔤 Name font", b"app:namefont"),
         Button.inline("🕐 Clock font", b"app:clockfont")],
        [Button.inline("🅰️ Custom name font", b"app:customname"),
         Button.inline("0️⃣ Custom clock font", b"app:customclock")],
        [Button.inline("◀️ Back", b"nav:main")],
    ]


def appearance_text(user_id: int = None) -> str:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    return (
        f"**Appearance**\n\n"
        f"Base name: `{settings.get('base_name', 'User')}`\n"
        f"Name font: {settings.get('name_font', 'normal')}\n"
        f"Clock font: {settings.get('clock_font', 'double')}\n"
        f"Custom name font: {'set' if settings.get('custom_name_font') else '—'}\n"
        f"Custom clock font: {'set' if settings.get('custom_clock_font') else '—'}"
    )


# ===========================================================================
# Jobs
# ===========================================================================
def jobs_buttons() -> List[List]:
    from .jobs import active_count
    return [
        [Button.inline(f"➕ New job ({active_count()})", b"job:new", style="success")],
        [Button.inline("📋 List jobs", b"job:list"),
         Button.inline("⏹ Stop all", b"job:stopall", style="danger")],
        [Button.inline("📁 Templates", b"job:templates")],
        [Button.inline("◀️ Back", b"nav:main")],
    ]


def jobs_text(user_id: int = None) -> str:
    from .jobs import active_jobs
    from .economy import cost_job

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    lines = ["**Jobs**", ""]
    jobs = active_jobs(owner_id=uid)
    if not jobs:
        lines.append("_No active jobs._")
    else:
        for j in jobs:
            lines.append(f"• `{j['id']}` every {j['interval']}s for {j['duration']}m — {j['sent']} sent")
    lines.append("")
    lines.append(f"Cost per new job: **{cost_job()} 💎**")
    return "\n".join(lines)


# ===========================================================================
# Job creation flow
# ===========================================================================
def job_setup_buttons() -> List[List]:
    return [
        [Button.inline("⚡ 1 min", b"job:interval:1", style="danger"),
         Button.inline("🕐 5 min", b"job:interval:5")],
        [Button.inline("⏰ 15 min", b"job:interval:15"),
         Button.inline("🕓 30 min", b"job:interval:30")],
        [Button.inline("🕕 60 min", b"job:interval:60")],
        [Button.inline("✏️ Custom interval", b"job:interval:custom", style="primary")],
        [Button.inline("◀️ Back", b"nav:jobs")],
    ]


def job_setup_text() -> str:
    return (
        "**New Repeat Job**\n\n"
        "**Step 1/3 — Interval**\n\n"
        "How often should the message be sent?\n\n"
        "Choose a preset or tap **Custom**."
    )


def job_duration_buttons(interval: int) -> List[List]:
    return [
        [Button.inline("⏱ 1 min", b"job:duration:1"),
         Button.inline("⏱ 5 min", b"job:duration:5")],
        [Button.inline("⏱ 30 min", b"job:duration:30"),
         Button.inline("⏱ 1 hour", b"job:duration:60")],
        [Button.inline("⏱ 3 hours", b"job:duration:180"),
         Button.inline("⏱ 6 hours", b"job:duration:360")],
        [Button.inline("⏱ 12 hours", b"job:duration:720")],
        [Button.inline("✏️ Custom duration", b"job:duration:custom", style="primary")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def job_duration_text(interval: int) -> str:
    return (
        "**New Repeat Job**\n\n"
        f"**Step 2/3 — Duration**\n\n"
        f"Interval: **{interval} min**\n\n"
        "How long should the job run?"
    )


def job_text_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Enter text", b"job:text:enter")],
        [Button.inline("📎 Upload .txt", b"job:text:upload")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def job_text_prompt() -> str:
    return (
        "**New Repeat Job**\n\n"
        "**Step 3/3 — Message**\n\n"
        "Send the text you want to repeat.\n\n"
        "**Variables:** `{time}` `{date}` `{job_id}` `{sent}` `{name}`"
    )


def job_confirm_buttons(job: dict) -> List[List]:
    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")

    can_start = (
        target is not None
        and interval >= 60
        and duration <= 720
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
    from .economy import cost_job

    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")

    warnings = []
    if target is None:
        warnings.append("❌ No target group selected")
    if interval < 60:
        warnings.append("❌ Min interval is **60 seconds**")
    if duration > 720:
        warnings.append("❌ Max duration is **720 minutes**")
    if not text:
        warnings.append("❌ No message text set")

    status = "\n".join(warnings) if warnings else "✅ Ready to start"

    return (
        "**Job Summary**\n\n"
        f"**Target:** `{target if target else '— not selected —'}`\n"
        f"**Interval:** `{interval}` s\n"
        f"**Duration:** `{duration}` min\n"
        f"**Cost:** `{cost_job()}` 💎\n\n"
        f"**Message:**\n`{text[:200] or '(empty)'}`\n\n"
        f"{status}"
    )


# ===========================================================================
# Group selection help
# ===========================================================================
def group_selection_buttons() -> List[List]:
    return [
        [Button.inline("📖 How to find group ID", b"job:groupid:help")],
        [Button.inline("◀️ Back", b"nav:jobs")],
    ]


def group_selection_text() -> str:
    return (
        "**🎯 Select Target Group**\n\n"
        "Send `/setgroup <chat_id>` to set the target group.\n\n"
        "**How to get the group ID:**\n"
        "Forward a message from that group to @userinfobot — "
        "it will show the group ID (e.g. `-1001234567890`)."
    )


def group_id_help_text() -> str:
    return (
        "**How to find a group ID**\n\n"
        "**Option 1 — @userinfobot:**\n"
        "1. Add @userinfobot to the group\n"
        "2. Send any message\n"
        "3. The bot replies with the group ID (starts with `-100`)\n\n"
        "**Option 2 — @RawDataBot:**\n"
        "1. Add @RawDataBot to the group\n"
        "2. Send any message\n"
        "3. The bot replies with full JSON — look for `chat.id`\n\n"
        "Then send to me: `/setgroup -1001234567890`"
    )


# ===========================================================================
# Account
# ===========================================================================
def account_buttons() -> List[List]:
    return [
        [Button.inline("💎 Request diamonds", b"acc:reqdiamonds")],
        [Button.inline("📜 My requests", b"acc:myreq")],
        [Button.inline("🤝 Referral", b"acc:referral")],
        [Button.inline("🔔 Notifications", b"acc:notify")],
        [Button.inline("🔐 QR Login", b"acc:qrlogin", style="primary")],
        [Button.inline("◀️ Back", b"nav:main")],
    ]


def account_text(user_id: int = None) -> str:
    from .economy import cost_clock, cost_job, get_balance

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    u = get_user(uid) or {}
    bal = get_balance(uid)
    return (
        f"**Account**\n\n"
        f"💎 Balance: **{bal}**\n"
        f"🆔 ID: `{uid}`\n"
        f"🤝 Referral: `{u.get('referral_code', '—')}`\n\n"
        f"Costs: update {cost_clock()} 💎 · job {cost_job()} 💎"
    )


# ===========================================================================
# Memory
# ===========================================================================
def memory_buttons() -> List[List]:
    return [
        [Button.inline("🧠 View memory", b"mem:view")],
        [Button.inline("🗑 Clear memory", b"mem:clear", style="danger"),
         Button.inline("♻️ Clear my state", b"mem:clearstate", style="danger")],
        [Button.inline("📤 Dump JSON", b"mem:dump")],
        [Button.inline("◀️ Back", b"nav:main")],
    ]


def memory_text() -> str:
    from .store import memory_store, user_state_store
    mem = memory_store.all()
    st = user_state_store.all()
    return (
        f"**Memory**\n\n"
        f"Memory keys: {len(mem) if isinstance(mem, dict) else 0}\n"
        f"User state keys: {len(st) if isinstance(st, dict) else 0}"
    )


# ===========================================================================
# Help
# ===========================================================================
def help_buttons() -> List[List]:
    return [[Button.inline("◀️ Back", b"nav:main")]]


def help_text() -> str:
    return (
        "**Help**\n\n"
        "**Commands:**\n"
        "/start — open the panel\n"
        "/help — this message\n"
        "/login — log in to your Telegram account\n"
        "/logout — log out\n"
        "/setgroup <chat_id> — set target group for jobs\n\n"
        "**Inside the panel:**\n"
        "• Clock ON/OFF — start/stop name updates\n"
        "• Settings — interval, timezone, language\n"
        "• Appearance — base name, fonts\n"
        "• Jobs — schedule repeated messages\n"
        "• Account — balance, requests, referral\n"
        "• Web Panel — open your account on the web\n\n"
        "**Repeat jobs (in bot DM):**\n"
        "`.rep <seconds> <minutes> <text>` — start\n"
        "`.stop` — stop all\n\n"
        "**Variables:** `{time}` `{date}` `{job_id}` `{sent}` `{name}`"
    )


# ===========================================================================
# Language picker
# ===========================================================================
def language_buttons() -> List[List]:
    return [
        [Button.inline("English", b"set:lang:en"),
         Button.inline("فارسی", b"set:lang:fa")],
        [Button.inline("◀️ Back", b"nav:settings")],
    ]


def language_text() -> str:
    return f"**Language / زبان**\n\nCurrent: {CONFIG.get('language', 'en')}"


# ===========================================================================
# Notifications
# ===========================================================================
def notify_buttons() -> List[List]:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    notify = u.get("notify") or {}

    def mark(k):
        return "✅" if notify.get(k, True) else "❌"

    return [
        [Button.inline(f"{mark('job_completion')} Job completion", b"notify:job_completion")],
        [Button.inline(f"{mark('diamond_changes')} Diamond changes", b"notify:diamond_changes")],
        [Button.inline(f"{mark('subscription_expiry')} Subscription expiry", b"notify:subscription_expiry")],
        [Button.inline("◀️ Back", b"nav:account")],
    ]


def notify_text() -> str:
    return "**Notifications**"


# ===========================================================================
# Backward-compat aliases
# ===========================================================================
def user_panel_buttons(user_id: int) -> List[List]:
    return main_panel_buttons()


def user_panel_text(user_id: int) -> str:
    return main_panel_text(user_id)


def user_account_buttons() -> List[List]:
    return account_buttons()


def user_account_text(user_id: int) -> str:
    return account_text(user_id)


def user_help_buttons() -> List[List]:
    return help_buttons()


def user_help_text() -> str:
    return help_text()