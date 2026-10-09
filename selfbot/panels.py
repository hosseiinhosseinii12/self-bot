"""Inline keyboard panels for the Telegram bot.

- Color-coded action buttons
- ALL Back buttons are colorless
- Job flow: group first (native Telegram picker), then interval/duration/text
"""
from typing import List, Optional

from .config import CONFIG
from .users import get_user, get_user_settings

# ---- Peer picker imports (best-effort) ----
HAS_PEER_PICKER = False
KeyboardButtonRequestPeer = None
RequestPeerTypeChat = None

try:
    from telethon.tl.types import (
        KeyboardButtonRequestPeer,
        RequestPeerTypeChat,
    )
    HAS_PEER_PICKER = True
except Exception as _e1:
    try:
        from telethon.tl.types.bots import (  # type: ignore
            KeyboardButtonRequestPeer,
            RequestPeerTypeChat,
        )
        HAS_PEER_PICKER = True
    except Exception as _e2:
        try:
            from .logging_setup import log_bot
            log_bot.warning(f"peer picker types unavailable: {_e1} / {_e2}")
        except Exception:
            pass

try:
    from telethon import Button
except ImportError:
    Button = None  # type: ignore


# ===========================================================================
# Font previews
# ===========================================================================
def _sample_name_font(font: str) -> str:
    try:
        from .fonts import apply_name_font
        return apply_name_font("Abc", font, custom="")
    except Exception:
        return "Abc"


def _sample_clock_font(font: str) -> str:
    try:
        from .fonts import apply_clock_font
        return apply_clock_font("123", font, custom="")
    except Exception:
        return "123"


def _back_btn(data: bytes = b"nav:main"):
    return Button.inline("◀️ Back", data)


# ===========================================================================
# Login
# ===========================================================================
def login_required_buttons() -> List[List]:
    return [
        [Button.inline("🔐 Login with phone", b"auth:start_login", style="success")],
        [Button.inline("📱 Login with QR code", b"auth:qr_login", style="primary")],
        [Button.inline("❓ Help", b"nav:help", style="primary")],
    ]


def login_required_text() -> str:
    return (
        "**🔐 Login required**\n\n"
        "You need to log in to your Telegram account first "
        "to use the bot features (clock, jobs, etc.).\n\n"
        "**Two ways to log in:**\n\n"
        "📱 **QR code** — fastest, no SMS needed\n"
        "   Open Telegram on your phone → Settings → Devices → "
        "Link Desktop Device → scan the QR.\n\n"
        "📞 **Phone number** — enter your number and wait for the code.\n\n"
        "_Your session is stored locally on the server._"
    )


# ===========================================================================
# Main panel
# ===========================================================================
def main_panel_buttons() -> List[List]:
    return [
        [Button.inline("🟢 Clock ON", b"clock:on", style="success"),
         Button.inline("🔴 Clock OFF", b"clock:off", style="danger")],
        [Button.inline("📊 Status", b"nav:status", style="primary")],
        [Button.inline("⚙️ Settings", b"nav:settings", style="primary"),
         Button.inline("🎨 Appearance", b"nav:appearance", style="primary")],
        [Button.inline("🔁 Jobs", b"nav:jobs", style="primary"),
         Button.inline("💎 Account", b"nav:account", style="primary")],
        [Button.inline("🌐 Web Panel", b"nav:webpanel", style="primary"),
         Button.inline("❓ Help", b"nav:help", style="primary")],
    ]


def main_panel_text(user_id: int = None) -> str:
    from .clock import last_written
    from .economy import cost_clock, cost_job, get_balance

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    state = "🟢 ON" if settings.get("clock_on") else "🔴 OFF"
    base = settings.get("base_name", "User")
    interval = settings.get("interval", 5)
    tz = settings.get("timezone", "Asia/Tehran")
    last = last_written(uid) or "—"
    bal = get_balance(uid)

    return (
        f"**🎛 Control Panel**\n\n"
        f"**Clock:** {state}\n"
        f"**Interval:** {interval} min\n"
        f"**Timezone:** {tz}\n"
        f"**Base name:** `{base}`\n"
        f"**Last name:** `{last}`\n\n"
        f"💎 **Balance:** {bal}\n"
        f"💸 Costs: update {cost_clock()} 💎 · new job {cost_job()} 💎"
    )


# ===========================================================================
# Status / Settings / Appearance
# ===========================================================================
def status_buttons() -> List[List]:
    return [[_back_btn(b"nav:main")]]


def status_text(user_id: int = None) -> str:
    from .clock import last_written
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    state = "🟢 ON" if settings.get("clock_on") else "🔴 OFF"
    return (
        f"**📊 Status**\n\n"
        f"**Clock:** {state}\n"
        f"**Interval:** {settings.get('interval', 5)} min\n"
        f"**Timezone:** {settings.get('timezone', 'Asia/Tehran')}\n"
        f"**Base name:** `{settings.get('base_name', 'User')}`\n"
        f"**Name font:** {settings.get('name_font', 'normal')}\n"
        f"**Clock font:** {settings.get('clock_font', 'double')}\n"
        f"**Last written:** `{last_written(uid) or '—'}`"
    )


def settings_buttons() -> List[List]:
    return [
        [Button.inline("⏱ Interval", b"set:interval", style="primary"),
         Button.inline("🌍 Timezone", b"set:timezone", style="primary")],
        [Button.inline("🗣 Language", b"set:language", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def settings_text(user_id: int = None) -> str:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    return (
        f"**⚙️ Settings**\n\n"
        f"**Interval:** {settings.get('interval', 5)} min\n"
        f"**Timezone:** {settings.get('timezone', 'Asia/Tehran')}\n"
        f"**Language:** {CONFIG.get('language', 'en')}"
    )


def appearance_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Base name", b"app:base", style="primary")],
        [Button.inline("🔤 Name font", b"app:namefont", style="primary"),
         Button.inline("🕐 Clock font", b"app:clockfont", style="primary")],
        [Button.inline("🅰️ Custom name font", b"app:customname", style="primary"),
         Button.inline("0️⃣ Custom clock font", b"app:customclock", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def appearance_text(user_id: int = None) -> str:
    from .fonts import apply_name_font, apply_clock_font
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    preview_name = apply_name_font(
        settings.get("base_name", "User"),
        settings.get("name_font", "normal"),
        custom=settings.get("custom_name_font", "") or "",
    )
    preview_clock = apply_clock_font(
        "12:34",
        settings.get("clock_font", "double"),
        custom=settings.get("custom_clock_font", "") or "",
    )
    return (
        f"**🎨 Appearance**\n\n"
        f"**Base name:** `{settings.get('base_name', 'User')}`\n"
        f"**Name font:** `{settings.get('name_font', 'normal')}`\n"
        f"**Clock font:** `{settings.get('clock_font', 'double')}`\n"
        f"**Custom name font:** {'✅ set' if settings.get('custom_name_font') else '—'}\n"
        f"**Custom clock font:** {'✅ set' if settings.get('custom_clock_font') else '—'}\n\n"
        f"**Preview:** `{preview_name} {preview_clock}`"
    )


# ===========================================================================
# Jobs list
# ===========================================================================
def jobs_buttons() -> List[List]:
    from .jobs import active_count
    return [
        [Button.inline(f"➕ New job ({active_count()})", b"job:new", style="success")],
        [Button.inline("📋 List jobs", b"job:list", style="primary"),
         Button.inline("⏹ Stop all", b"job:stopall", style="danger")],
        [_back_btn(b"nav:main")],
    ]


def jobs_text(user_id: int = None) -> str:
    from .jobs import active_jobs
    from .economy import cost_job

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    lines = ["**🔁 Jobs**", ""]
    jobs = active_jobs(owner_id=uid)
    if not jobs:
        lines.append("_No active jobs._")
    else:
        for j in jobs:
            dur = "∞" if int(j.get("duration", 0) or 0) == 0 else f"{j['duration']}m"
            lines.append(
                f"• `{j['id']}` every {j['interval']}s for {dur} — {j['sent']} sent"
            )
    lines.append("")
    lines.append(f"💸 Cost per job: **{cost_job()} 💎**")
    return "\n".join(lines)


# ===========================================================================
# JOB FLOW — Step 1: Group
# ===========================================================================
def job_group_buttons(has_recent: bool = False) -> List[List]:
    rows = []
    if has_recent:
        rows.append([Button.inline("🔁 Reuse last group", b"job:group:reuse", style="primary")])
    if HAS_PEER_PICKER:
        rows.append([Button.inline("🎯 Select a group", b"job:group:select", style="success")])
    else:
        rows.append([Button.inline("✏️ Enter chat ID", b"job:group:manual", style="success")])
    rows.append([_back_btn(b"nav:jobs")])
    return rows


def job_group_text(has_recent: bool = False, recent_id: int = None) -> str:
    txt = (
        "**🔁 New Repeat Job**\n\n"
        "**Step 1/4 — Target group**\n\n"
        "Which group should the messages be sent to?\n\n"
    )
    if HAS_PEER_PICKER:
        txt += "Tap **🎯 Select a group** and pick from the Telegram UI."
    else:
        txt += "Tap **✏️ Enter chat ID** and send the group's ID."
    if has_recent and recent_id:
        txt += f"\n\n_Last used: `{recent_id}`_"
    return txt


# ===========================================================================
# JOB FLOW — Step 2: Interval
# ===========================================================================
def job_interval_buttons() -> List[List]:
    return [
        [Button.inline("⚡ 1 min", b"job:interval:1", style="danger"),
         Button.inline("🕐 5 min", b"job:interval:5", style="primary")],
        [Button.inline("⏰ 15 min", b"job:interval:15", style="primary"),
         Button.inline("🕓 30 min", b"job:interval:30", style="primary")],
        [Button.inline("🕕 60 min", b"job:interval:60", style="primary")],
        [Button.inline("✏️ Custom interval", b"job:interval:custom", style="success")],
        [_back_btn(b"job:setup:back")],
    ]


def job_interval_text(target_id: int) -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 2/4 — Interval**\n\n"
        f"✅ Target: `{target_id}`\n\n"
        "How often should the message be sent?"
    )


# ===========================================================================
# JOB FLOW — Step 3: Duration
# ===========================================================================
def job_duration_buttons() -> List[List]:
    return [
        [Button.inline("⏱ 1 min", b"job:duration:1", style="primary"),
         Button.inline("⏱ 5 min", b"job:duration:5", style="primary")],
        [Button.inline("⏱ 30 min", b"job:duration:30", style="primary"),
         Button.inline("⏱ 1 hour", b"job:duration:60", style="primary")],
        [Button.inline("⏱ 3 hours", b"job:duration:180", style="primary"),
         Button.inline("⏱ 6 hours", b"job:duration:360", style="primary")],
        [Button.inline("⏱ 12 hours", b"job:duration:720", style="primary")],
        [Button.inline("♾ Unlimited (until stop)", b"job:duration:0", style="success")],
        [Button.inline("✏️ Custom duration", b"job:duration:custom", style="success")],
        [_back_btn(b"job:setup:back")],
    ]


def job_duration_text(target_id: int, interval_min: int) -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 3/4 — Duration**\n\n"
        f"✅ Target: `{target_id}`\n"
        f"✅ Interval: {interval_min} min\n\n"
        "How long should the job run?\n\n"
        "Tap **♾ Unlimited** to run until you stop it."
    )


# ===========================================================================
# JOB FLOW — Step 4: Message
# ===========================================================================
def job_text_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Enter text", b"job:text:enter", style="primary")],
        [Button.inline("📎 Upload .txt", b"job:text:upload", style="success")],
        [_back_btn(b"job:setup:back")],
    ]


def job_text_prompt(target_id: int = None, interval_s: int = 0,
                    duration_m: int = 0) -> str:
    lines = [
        "**🔁 New Repeat Job**",
        "",
        "**Step 4/4 — Message**",
        "",
    ]
    if target_id:
        lines.append(f"✅ Target: `{target_id}`")
    if interval_s:
        lines.append(f"✅ Interval: {interval_s}s")
    if duration_m is not None:
        dur_str = "∞" if duration_m == 0 else f"{duration_m} min"
        lines.append(f"✅ Duration: {dur_str}")
    lines.append("")
    lines.append("Send the text you want to repeat.")
    lines.append("")
    lines.append("**Variables:** `{time}` `{date}` `{job_id}` `{sent}` `{name}`")
    return "\n".join(lines)


# ===========================================================================
# JOB FLOW — Step 5: Confirm
# ===========================================================================
def job_confirm_buttons(job: dict) -> List[List]:
    interval = job.get("interval", 0)
    text = job.get("text", "")
    target = job.get("target")
    can_start = (target is not None and interval >= 60 and text)
    rows = []
    if can_start:
        rows.append([Button.inline("✅ Start job", b"job:confirm:start", style="success")])
    else:
        rows.append([Button.inline("⚠️ Cannot start", b"job:confirm:blocked", style="danger")])
    rows.append([_back_btn(b"job:setup:back")])
    rows.append([Button.inline("❌ Cancel", b"nav:jobs", style="danger")])
    return rows


def job_confirm_text(job: dict) -> str:
    from .economy import cost_job

    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")

    duration_str = "∞ (unlimited)" if duration == 0 else f"{duration} min"

    warnings = []
    if target is None:
        warnings.append("❌ No target group")
    if interval < 60:
        warnings.append("❌ Min interval: 60 seconds")
    if not text:
        warnings.append("❌ No message text set")
    status = "\n".join(warnings) if warnings else "✅ Ready to start"

    return (
        "**🔁 Job Summary**\n\n"
        f"🎯 **Target:** `{target if target else '— not selected —'}`\n"
        f"⏱ **Interval:** `{interval}` s\n"
        f"⏳ **Duration:** `{duration_str}`\n"
        f"💸 **Cost:** `{cost_job()}` 💎\n\n"
        f"📝 **Message:**\n`{text[:200] or '(empty)'}`\n\n"
        f"{status}"
    )


# ===========================================================================
# Peer Picker — Telegram native UI
# ===========================================================================
def make_peer_picker_button(button_id: int, label: str = "🎯 Select a group"):
    """Return a KeyboardButtonRequestPeer instance, or None if unavailable."""
    if not HAS_PEER_PICKER:
        return None
    if KeyboardButtonRequestPeer is None or RequestPeerTypeChat is None:
        return None
    try:
        return KeyboardButtonRequestPeer(
            text=label,
            button_id=button_id,
            peer_type=RequestPeerTypeChat(),
            max_quantity=1,
        )
    except Exception as e:
        try:
            from .logging_setup import log_bot
            log_bot.warning(f"could not construct KeyboardButtonRequestPeer: {e}")
        except Exception:
            pass
        return None


def group_selector_text() -> str:
    return (
        "**🎯 Select Target Group**\n\n"
        "Tap the **🎯 Group** button that appeared **below the input field**.\n\n"
        "Telegram will open your chat list — pick a group."
    )


def group_selector_help_buttons() -> List[List]:
    return [
        [_back_btn(b"nav:jobs")],
    ]


# ===========================================================================
# Account
# ===========================================================================
def account_buttons() -> List[List]:
    return [
        [Button.inline("💎 Request diamonds", b"acc:reqdiamonds", style="primary")],
        [Button.inline("📜 My requests", b"acc:myreq", style="primary"),
         Button.inline("🤝 Referral", b"acc:referral", style="primary")],
        [Button.inline("🔔 Notifications", b"acc:notify", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def account_text(user_id: int = None) -> str:
    from .economy import cost_clock, cost_job, get_balance

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    u = get_user(uid) or {}
    bal = get_balance(uid)
    return (
        f"**💎 Account**\n\n"
        f"💎 **Balance:** {bal}\n"
        f"🆔 **ID:** `{uid}`\n"
        f"🤝 **Referral:** `{u.get('referral_code', '—')}`\n\n"
        f"💸 Costs: update {cost_clock()} 💎 · job {cost_job()} 💎"
    )


# ===========================================================================
# Help / Language / Notifications
# ===========================================================================
def help_buttons() -> List[List]:
    return [[_back_btn(b"nav:main")]]


def help_text() -> str:
    return (
        "**❓ Help**\n\n"
        "**Commands:**\n"
        "`/start` — open the panel\n"
        "`/help` — this message\n"
        "`/login` — log in to your Telegram account\n"
        "`/logout` — log out\n\n"
        "**Inside the panel:**\n"
        "• 🟢 Clock ON / 🔴 Clock OFF\n"
        "• ⚙️ Settings — interval, timezone\n"
        "• 🎨 Appearance — base name, fonts\n"
        "• 🔁 Jobs — schedule repeated messages\n"
        "• 💎 Account — balance, requests\n"
        "• 🌐 Web Panel — open your account on the web\n\n"
        "**Variables:** `{time}` `{date}` `{job_id}` `{sent}` `{name}`"
    )


def language_buttons() -> List[List]:
    return [
        [Button.inline("English", b"set:lang:en", style="primary"),
         Button.inline("فارسی", b"set:lang:fa", style="primary")],
        [_back_btn(b"nav:settings")],
    ]


def language_text() -> str:
    return f"**🗣 Language / زبان**\n\nCurrent: {CONFIG.get('language', 'en')}"


def notify_buttons() -> List[List]:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    notify = u.get("notify") or {}

    def mark(k):
        return "✅" if notify.get(k, True) else "❌"

    return [
        [Button.inline(f"{mark('job_completion')} Job completion",
                       b"notify:job_completion", style="primary")],
        [Button.inline(f"{mark('diamond_changes')} Diamond changes",
                       b"notify:diamond_changes", style="primary")],
        [Button.inline(f"{mark('subscription_expiry')} Subscription expiry",
                       b"notify:subscription_expiry", style="primary")],
        [_back_btn(b"nav:account")],
    ]


def notify_text() -> str:
    return "**🔔 Notifications**"