"""QR Code Login flow for Telegram.

Uses Telethon's native client.qr_login(). Avoids importing raw
AuthLoginToken classes (their import path changed across Telethon versions).
"""
import asyncio
import base64
import io
import time
from typing import Optional

from .config import CONFIG, DB_PATH, get_api_credentials
from .logging_setup import log_bot
from .store import user_state_store


# ===========================================================================
# Diagnostics
# ===========================================================================
TELETHON_ERROR: Optional[str] = None
TELETHON_VERSION: Optional[str] = None
QRCODE_ERROR: Optional[str] = None

try:
    import telethon  # noqa: F401
    TELETHON_VERSION = getattr(telethon, "__version__", "unknown")
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    HAS_TELETHON = True
    log_bot.info(f"qr_login: telethon {TELETHON_VERSION} loaded")
except Exception as _e:
    HAS_TELETHON = False
    TELETHON_ERROR = f"{type(_e).__name__}: {_e}"
    log_bot.error(f"qr_login: telethon import failed — {TELETHON_ERROR}")

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except Exception as _e:
    HAS_QRCODE = False
    QRCODE_ERROR = f"{type(_e).__name__}: {_e}"
    log_bot.warning(f"qr_login: qrcode import failed — {QRCODE_ERROR}")


def diagnose() -> dict:
    return {
        "telethon": HAS_TELETHON,
        "telethon_version": TELETHON_VERSION,
        "telethon_error": TELETHON_ERROR,
        "qrcode": HAS_QRCODE,
        "qrcode_error": QRCODE_ERROR,
    }


# ===========================================================================
# PNG rendering
# ===========================================================================
def _render_qr_png(url: str) -> bytes:
    if not HAS_QRCODE:
        return b""
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log_bot.error(f"qr png render failed: {e}")
        return b""


# ===========================================================================
# State
# ===========================================================================
def _save_qr_state(expires_at: float) -> None:
    try:
        user_state_store.set("qr_login", {"expires_at": expires_at})
    except Exception:
        pass


def _clear_qr_state() -> None:
    try:
        user_state_store.delete("qr_login")
    except Exception:
        pass


# ===========================================================================
# Public API
# ===========================================================================
async def start_qr_login(runtime) -> tuple:
    """Start QR login. Returns (ok, msg_or_url, png, expires_at)."""
    if not HAS_TELETHON:
        return False, f"Telethon import failed: {TELETHON_ERROR or 'unknown'}", b"", 0
    if not HAS_QRCODE:
        return False, f"qrcode import failed: {QRCODE_ERROR or 'not installed'}", b"", 0

    # --- build a proxy-aware client ---
    client = getattr(runtime, "user_client", None)
    fresh = False
    if client is None:
        try:
            from .bootstrap import make_user_client_for_login
            client = await make_user_client_for_login()
            fresh = True
        except Exception as e:
            log_bot.error(f"qr: failed to create user client: {e}")
            return False, f"Failed to create client: {type(e).__name__}: {e}", b"", 0

    # --- try native client.qr_login() ---
    if not hasattr(client, "qr_login"):
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "Telethon client has no qr_login() method.", b"", 0

    try:
        qr_obj = await client.qr_login()
    except Exception as e:
        log_bot.error(f"qr: client.qr_login() failed: {type(e).__name__}: {e}")
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, f"Failed to start QR login: {type(e).__name__}: {e}", b"", 0

    qr_url = getattr(qr_obj, "url", None)
    if not qr_url:
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "QR object has no url.", b"", 0

    log_bot.info(f"qr: got url (len={len(qr_url)})")

    expires_at = time.time() + 60.0

    png = _render_qr_png(qr_url)
    if not png:
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "Failed to render QR PNG.", b"", 0

    runtime._qr_client = client
    runtime._qr_expires_at = expires_at
    _save_qr_state(expires_at)

    # Start the waiter — no raw class imports needed
    asyncio.create_task(_wait_native(runtime, qr_obj))

    log_bot.info(f"qr: started (expires_at={expires_at})")
    return True, qr_url, png, expires_at


# ===========================================================================
# Waiter (uses only qr_obj.wait())
# ===========================================================================
async def _wait_native(runtime, qr_obj) -> None:
    owner_id = int(CONFIG.get("owner_id", 338266658))
    client = runtime._qr_client

    try:
        await asyncio.wait_for(qr_obj.wait(), timeout=90.0)
    except asyncio.TimeoutError:
        await _notify(runtime, owner_id, "⏱ QR code expired. Send /login to try again.")
        _clear_qr_state()
        return
    except SessionPasswordNeededError:
        await _notify(runtime, owner_id,
                      "✅ QR scanned. 🔐 2FA required.\nSend your 2FA password now.")
        runtime._qr_needs_2fa = True
        rt_state = getattr(runtime, "_state", {})
        rt_state.setdefault("awaiting", {})["qr_2fa_password"] = True
        try:
            runtime.save_state()
        except Exception:
            pass
        return
    except Exception as e:
        log_bot.error(f"qr wait_native failed: {type(e).__name__}: {e}")
        await _notify(runtime, owner_id, f"❌ QR login failed: {e}")
        _clear_qr_state()
        return

    # Success — qr_obj.wait() already called client.sign_in internally
    runtime.user_client = client
    _clear_qr_state()
    log_bot.info("qr: login succeeded (no 2FA)")

    # Explicitly check authorization to be safe
    try:
        authorized = await client.is_user_authorized()
        log_bot.info(f"qr: is_user_authorized={authorized}")
    except Exception:
        pass

    await _notify(runtime, owner_id,
                  "✅ Logged in via QR code.\nYou can now use /on, /name, etc.")
    try:
        if CONFIG.get("clock_on"):
            from .clock import start_clock
            start_clock(client)
    except Exception:
        pass


# ===========================================================================
# Finish 2FA after QR
# ===========================================================================
async def finish_qr_2fa(runtime, password: str) -> tuple:
    """Called by bot_handlers when the owner sends the 2FA password."""
    client = getattr(runtime, "_qr_client", None)
    if client is None:
        return False, "No pending QR login."
    try:
        await client.sign_in(password=password)
    except Exception as e:
        return False, f"2FA failed: {type(e).__name__}: {e}"
    runtime.user_client = client
    runtime._qr_needs_2fa = False
    _clear_qr_state()
    try:
        if CONFIG.get("clock_on"):
            from .clock import start_clock
            start_clock(client)
    except Exception:
        pass
    return True, "OK"


# ===========================================================================
# Notify
# ===========================================================================
async def _notify(runtime, owner_id: int, text: str) -> None:
    try:
        if runtime.bot_client:
            await runtime.bot_client.send_message(owner_id, text)
    except Exception:
        pass