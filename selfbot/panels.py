"""Inline keyboard panels for the Telegram bot (English + Persian)."""
from typing import List, Optional

from .config import CONFIG
from .i18n import t
from .subscriptions import days_left, effective_plan
from .users import get_user

try:
    from telethon import Button
except ImportError:
    Button = None  # type: ignore


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
def main_panel_buttons() -> List[List]:
    return [
        [Button.inline(t("btn_on"), b"clock:on"),
         Button.inline(t("btn_off"), b"clock:off")],
        [Button.inline(t("btn_status"), b"nav:status")],
        [Button.inline(t("btn_settings"), b"nav:settings"),
         Button.inline(t("btn_appearance"), b"nav:appearance")],
        [Button.inline(t("btn_jobs"), b"nav:jobs"),
         Button.inline(t("btn_account"), b"nav:account")],
        [Button.inline(t("btn_memory"), b"nav:memory"),
         Button.inline(t("btn_help"), b"nav:help")],
    ]


def main_panel_text() -> str:
    from .clock import last_written
    state = "🟢 ON" if CONFIG.get("clock_on") else "🔴 OFF"
    base = CONFIG.get("base_name", "User")
    interval = CONFIG.get("interval", 5)
    tz = CONFIG.get("timezone", "UTC")
    last = last_written() or "—"
    return (
        f"**{t('main_panel')}**\n\n"
        f"Clock: {state}\n"
        f"{t('interval')}: {interval} min\n"
        f"{t('timezone')}: {tz}\n"
        f"{t('base_name')}: `{base}`\n"
        f"Last name: `{last}`"
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def settings_buttons() -> List[List]:
    return [
        [Button.inline("⏱ " + t("interval"), b"set:interval"),
         Button.inline("🌍 " + t("timezone"), b"set:timezone")],
        [Button.inline("🗣 " + t("language"), b"set:language")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def settings_text() -> str:
    return (
        f"**{t('btn_settings')}**\n\n"
        f"{t('interval')}: {CONFIG.get('interval', 5)} min\n"
        f"{t('timezone')}: {CONFIG.get('timezone', 'UTC')}\n"
        f"{t('language')}: {CONFIG.get('language', 'en')}"
    )


# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------
def appearance_buttons() -> List[List]:
    return [
        [Button.inline("✏️ " + t("base_name"), b"app:base")],
        [Button.inline("🔤 " + t("name_font"), b"app:namefont"),
         Button.inline("🕐 " + t("clock_font"), b"app:clockfont")],
        [Button.inline("🅰️ Custom name font", b"app:customname"),
         Button.inline("0️⃣ Custom clock font", b"app:customclock")],
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


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def jobs_buttons() -> List[List]:
    from .jobs import active_count
    from .subscriptions import plan_limits
    owner = int(CONFIG.get("owner_id", 338266658))
    lim = plan_limits(owner)
    return [
        [Button.inline(f"➕ New job ({active_count()}/{lim['max_jobs']})", b"job:new")],
        [Button.inline("📋 " + t("btn_jobs"), b"job:list"),
         Button.inline("⏹ Stop all", b"job:stopall")],
        [Button.inline("📁 Templates", b"job:templates")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def jobs_text() -> str:
    from .jobs import active_jobs, active_count
    from .subscriptions import plan_limits
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
    lines.append(
        f"Plan: {effective_plan(owner)} — max jobs {lim['max_jobs']}, "
        f"min interval {lim['min_interval']}s, max duration {lim['max_duration']}m"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------
def account_buttons() -> List[List]:
    return [
        [Button.inline("💎 " + t("request_diamonds"), b"acc:reqdiamonds")],
        [Button.inline("⭐ " + t("request_subscription"), b"acc:reqsub")],
        [Button.inline("📜 " + t("my_requests"), b"acc:myreq")],
        [Button.inline("🤝 " + t("your_referral").split(":")[0], b"acc:referral")],
        [Button.inline("🔔 " + t("notify_prefs"), b"acc:notify")],
        [Button.inline(t("btn_back"), b"nav:main")],
    ]


def account_text() -> str:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    plan = effective_plan(owner)
    days = days_left(owner)
    bal = int(u.get("diamonds", 0))
    return (
        f"**{t('btn_account')}**\n\n"
        f"{t('diamonds')}: {bal} 💎\n"
        f"{t('plan')}: {plan}\n"
        f"{t('days_left')}: {days}\n"
        f"{t('your_referral', code=u.get('referral_code', '—'))}"
    )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
def memory_buttons() -> List[List]:
    return [
        [Button.inline("🧠 View memory", b"mem:view")],
        [Button.inline("🗑 Clear memory", b"mem:clear"),
         Button.inline("♻️ Clear my state", b"mem:clearstate")],
        [Button.inline("📤 Dump JSON", b"mem:dump")],
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


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
def help_buttons() -> List[List]:
    return [[Button.inline(t("btn_back"), b"nav:main")]]


def help_text() -> str:
    return (
        "**Help**\n\n"
        "Commands:\n"
        "/start — main panel\n"
        "/login — log in to a Telegram account\n"
        "/logout — delete session\n"
        "/account — diamonds + subscription\n"
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
        "Repeat jobs (owner only, in bot DM):\n"
        "`.rep <seconds> <minutes> <text>` — start a repeat job\n"
        "`.stop` — stop jobs in this chat"
    )


# ---------------------------------------------------------------------------
# Language picker
# ---------------------------------------------------------------------------
def language_buttons() -> List[List]:
    return [
        [Button.inline("English", b"set:lang:en"),
         Button.inline("فارسی", b"set:lang:fa")],
        [Button.inline(t("btn_back"), b"nav:settings")],
    ]


def language_text() -> str:
    return (
        "**Language / زبان**\n\n"
        f"Current: {CONFIG.get('language', 'en')}"
    )


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------
def notify_buttons() -> List[List]:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    notify = u.get("notify") or {}
    def mark(k):
        return "✅" if notify.get(k, True) else "❌"
    return [
        [Button.inline(f"{mark('job_completion')} {t('notify_job')}", b"notify:job_completion")],
        [Button.inline(f"{mark('diamond_changes')} {t('notify_diamond')}", b"notify:diamond_changes")],
        [Button.inline(f"{mark('subscription_expiry')} {t('notify_expiry')}", b"notify:subscription_expiry")],
        [Button.inline(t("btn_back"), b"nav:account")],
    ]


def notify_text() -> str:
    return f"**{t('notify_prefs')}**"


# ---------------------------------------------------------------------------
# Plan picker (for requests)
# ---------------------------------------------------------------------------
def plan_buttons() -> List[List]:
    return [
        [Button.inline("Basic — 30 days", b"reqsub:basic")],
        [Button.inline("Pro — 30 days", b"reqsub:pro")],
        [Button.inline("VIP — 30 days", b"reqsub:vip")],
        [Button.inline(t("btn_back"), b"nav:account")],
    ]


def plan_text() -> str:
    return (
        f"**{t('request_subscription')}**\n\n"
        "Pick a plan. An admin will review your request."
    )