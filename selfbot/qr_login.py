"""QR Code Login flow for Telegram.

Uses Telethon's native client.qr_login() when available, falls back to
manual ExportLoginTokenRequest if the native method is missing.

Every import is guarded so we can report *which* one fails.
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
# Diagnostics — find out exactly what fails
# ===========================================================================
TELETHON_ERROR: Optional[str] = None
TELETHON_VERSION: Optional[str] = None
QRCODE_ERROR: Optional[str] = None
_HAS_QR_TYPES = False

try:
    import telethon  # noqa: F401
    TELETHON_VERSION = getattr(telethon, "__version__", "unknown")

    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    # Try to import QR-specific types. These live under .types.auth
    # (not .types) in Telethon >= 1.24.
    try:
        from telethon.tl.functions.auth import (ExportLoginTokenRequest,
                                                  AcceptLoginTokenRequest)
        from telethon.tl.types.auth import (AuthLoginToken,
                                             AuthLoginTokenMigrateTo,
                                             AuthLoginTokenSuccess)
        _HAS_QR_TYPES = True
    except ImportError:
        # Fallback: try the flat path (some versions re-export)
        try:
            from telethon.tl.functions.auth import (ExportLoginTokenRequest,
                                                      AcceptLoginTokenRequest)
            from telethon.tl.types import (AuthLoginToken,
                                            AuthLoginTokenMigrateTo,
                                            AuthLoginTokenSuccess)
            _HAS_QR_TYPES = True
        except ImportError as _e:
            _HAS_QR_TYPES = False
            log_bot.warning(
                f"qr_login: QR types missing (need Telethon>=1.24): {_e} "
                f"(installed version: {TELETHON_VERSION})"
            )

    HAS_TELETHON = True
    log_bot.info(
        f"qr_login: telethon {TELETHON_VERSION} loaded, QR types={_HAS_QR_TYPES}"
    )
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
    """Return a dict describing which dependency is missing or too old."""
    return {
        "telethon": HAS_TELETHON,
        "telethon_version": TELETHON_VERSION,
        "telethon_error": TELETHON_ERROR,
        "qr_types": _HAS_QR_TYPES,
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
# State persistence
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
    """Start QR login. Returns (ok, msg_or_url, png, expires_at).

    - ok=True:  msg is the tg:// URL, png is the QR PNG, expires_at epoch
    - ok=False: msg is the error message
    """
    # --- dependency checks ---
    if not HAS_TELETHON:
        return False, f"Telethon import failed: {TELETHON_ERROR or 'unknown'}", b"", 0
    if not HAS_QRCODE:
        return False, f"qrcode import failed: {QRCODE_ERROR or 'not installed'}", b"", 0

    api_id, api_hash = get_api_credentials()

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

    # --- try native client.qr_login() first ---
    qr_url = None
    qr_obj = None
    expires_at = time.time() + 60.0

    if hasattr(client, "qr_login"):
        try:
            qr_obj = await client.qr_login()
            qr_url = getattr(qr_obj, "url", None)
            log_bot.info(f"qr: native client.qr_login() ok, url={qr_url!r}")
        except Exception as e:
            log_bot.warning(f"qr: native client.qr_login() failed: {e}")
            qr_obj = None
            qr_url = None

    # --- fallback to manual ExportLoginTokenRequest ---
    if qr_url is None:
        if not _HAS_QR_TYPES:
            if fresh:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return (False,
                    f"Telethon too old (need >=1.24 for AuthLoginToken). "
                    f"Installed: {TELETHON_VERSION}", b"", 0)

        try:
            result = await client(ExportLoginTokenRequest(
                api_id=api_id,
                api_hash=api_hash,
                except_ids=[],
            ))
        except Exception as e:
            log_bot.error(f"qr: ExportLoginTokenRequest failed: {e}")
            if fresh:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return False, f"Failed to export login token: {type(e).__name__}: {e}", b"", 0

        if isinstance(result, AuthLoginTokenSuccess):
            runtime.user_client = client
            _clear_qr_state()
            return True, "Already logged in.", b"", 0

        if isinstance(result, AuthLoginTokenMigrateTo):
            try:
                await client.disconnect()
                await client.connect(result.dc_id)
                result = await client(ExportLoginTokenRequest(
                    api_id=api_id, api_hash=api_hash, except_ids=[],
                ))
            except Exception as e:
                if fresh:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                return False, f"DC migrate failed: {e}", b"", 0

        if not isinstance(result, AuthLoginToken):
            if fresh:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return False, "Unexpected response from Telegram.", b"", 0

        token_b64 = base64.urlsafe_b64encode(result.token).rstrip(b"=").decode()
        qr_url = f"tg://login?token={token_b64}"
        expires_at = float(result.expires)
        asyncio.create_task(_poll_manual(runtime, client, api_id, api_hash))

    # --- render PNG ---
    png = _render_qr_png(qr_url)
    if not png:
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "Failed to render QR PNG.", b"", 0

    # --- persist state ---
    runtime._qr_client = client
    runtime._qr_expires_at = expires_at
    _save_qr_state(expires_at)

    # If we used native client.qr_login(), start its waiter
    if qr_obj is not None:
        asyncio.create_task(_wait_native(runtime, qr_obj))

    log_bot.info(f"qr: started (expires_at={expires_at})")
    return True, qr_url, png, expires_at


# ===========================================================================
# Native waiter (client.qr_login())
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

    # Success
    runtime.user_client = client
    _clear_qr_state()
    log_bot.info("qr: login succeeded (no 2FA)")
    await _notify(runtime, owner_id,
                  "✅ Logged in via QR code.\nYou can now use /on, /name, etc.")
    try:
        if CONFIG.get("clock_on"):
            from .clock import start_clock
            start_clock(client)
    except Exception:
        pass


# ===========================================================================
# Manual poller (fallback path)
# ===========================================================================
async def _poll_manual(runtime, client, api_id: int, api_hash: str) -> None:
    owner_id = int(CONFIG.get("owner_id", 338266658))
    while True:
        if time.time() > getattr(runtime, "_qr_expires_at", 0):
            await _notify(runtime, owner_id, "⏱ QR code expired. Send /login to try again.")
            _clear_qr_state()
            return
        await asyncio.sleep(3)
        try:
            result = await client(ExportLoginTokenRequest(
                api_id=api_id, api_hash=api_hash, except_ids=[],
            ))
        except Exception as e:
            log_bot.debug(f"qr poll error: {e}")
            continue

        if isinstance(result, AuthLoginTokenSuccess):
            runtime.user_client = client
            _clear_qr_state()
            log_bot.info("qr: login succeeded (manual poll)")
            await _notify(runtime, owner_id,
                          "✅ Logged in via QR code.\nYou can now use /on, /name, etc.")
            return

        if isinstance(result, AuthLoginTokenMigrateTo):
            try:
                await client.disconnect()
                await client.connect(result.dc_id)
            except Exception:
                pass
            continue

        if isinstance(result, AuthLoginToken):
            runtime._qr_expires_at = float(result.expires)
            _save_qr_state(result.expires)
            continue


# ===========================================================================
# Finish 2FA after QR
# ===========================================================================
async def finish_qr_2fa(runtime, password: str) -> tuple:
    """Called by bot_handlers when the owner sends the 2FA password after QR scan."""
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
# Notify helper
# ===========================================================================
async def _notify(runtime, owner_id: int, text: str) -> None:
    try:
        if runtime.bot_client:
            await runtime.bot_client.send_message(owner_id, text)
    except Exception:
        pass