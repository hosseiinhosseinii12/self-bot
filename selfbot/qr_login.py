"""QR Code Login flow for Telegram.

Uses Telethon's ExportLoginTokenRequest to produce a QR URL, renders it
as a PNG, and lets the owner scan it with the official Telegram app.
"""
import asyncio
import base64
import io
import time
from typing import Optional

from .config import CONFIG, DB_PATH, get_api_credentials
from .logging_setup import log_bot
from .store import user_state_store

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl.functions.auth import (ExportLoginTokenRequest,
                                             AcceptLoginTokenRequest)
    from telethon.tl.types import (AuthLoginToken,
                                    AuthLoginTokenMigrateTo,
                                    AuthLoginTokenSuccess)
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ---------------------------------------------------------------------------
# Render a QR URL to PNG bytes
# ---------------------------------------------------------------------------
def _render_qr_png(url: str) -> bytes:
    if not HAS_QRCODE:
        return b""
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


# ---------------------------------------------------------------------------
# Persist QR session state
# ---------------------------------------------------------------------------
def _save_qr_state(token_bytes_hex: str, expires_at: float) -> None:
    user_state_store.set("qr_login", {
        "token": token_bytes_hex,
        "expires_at": expires_at,
    })


def _load_qr_state() -> Optional[dict]:
    st = user_state_store.get("qr_login")
    if not isinstance(st, dict):
        return None
    if st.get("expires_at", 0) < time.time():
        return None
    return st


def _clear_qr_state() -> None:
    try:
        user_state_store.delete("qr_login")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Start QR login
# ---------------------------------------------------------------------------
async def start_qr_login(runtime) -> tuple:
    """Start QR login. Returns (ok, message, png_bytes, expires_at)."""
    if not HAS_TELETHON:
        return False, "Telethon not available.", b"", 0
    if not HAS_QRCODE:
        return False, "qrcode library not installed.", b"", 0

    api_id, api_hash = get_api_credentials()

    # Use existing user_client or create a fresh one
    client = runtime.user_client
    fresh = False
    if client is None:
        session_path = str(DB_PATH / "user.session")
        client = TelegramClient(session_path, api_id, api_hash)
        await client.connect()
        fresh = True

    try:
        result = await client(ExportLoginTokenRequest(
            api_id=api_id,
            api_hash=api_hash,
            except_ids=[],
        ))
    except Exception as e:
        log_bot.error(f"QR export token failed: {e}")
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, f"Failed to start QR login: {e}", b"", 0

    if isinstance(result, AuthLoginTokenSuccess):
        runtime.user_client = client
        _clear_qr_state()
        return True, "Already logged in.", b"", 0

    if isinstance(result, AuthLoginTokenMigrateTo):
        try:
            dc_id = result.dc_id
            await client.disconnect()
            await client.connect(dc_id)
            result = await client(ExportLoginTokenRequest(
                api_id=api_id,
                api_hash=api_hash,
                except_ids=[],
            ))
        except Exception as e:
            log_bot.error(f"QR migrate failed: {e}")
            if fresh:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return False, f"QR migrate failed: {e}", b"", 0

    if not isinstance(result, AuthLoginToken):
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "Unexpected response from Telegram.", b"", 0

    # Build the tg:// login URL
    token_b64 = base64.urlsafe_b64encode(result.token).rstrip(b"=").decode()
    url = f"tg://login?token={token_b64}"
    expires_at = float(result.expires)

    png = _render_qr_png(url)
    if not png:
        if fresh:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, "Failed to render QR image.", b"", 0

    runtime._qr_client = client
    runtime._qr_token_hex = result.token.hex()
    runtime._qr_expires_at = expires_at
    _save_qr_state(result.token.hex(), expires_at)

    log_bot.info(f"QR login started, expires at {expires_at}")

    asyncio.create_task(_poll_qr_login(runtime))

    return True, url, png, expires_at


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------
async def _poll_qr_login(runtime) -> None:
    """Poll Telegram every 3s until the QR is accepted or expires."""
    client = getattr(runtime, "_qr_client", None)
    if client is None:
        return

    api_id, api_hash = get_api_credentials()
    owner_id = int(CONFIG.get("owner_id", 338266658))

    while True:
        if time.time() > getattr(runtime, "_qr_expires_at", 0):
            log_bot.info("QR login expired")
            try:
                if runtime.bot_client:
                    await runtime.bot_client.send_message(
                        owner_id,
                        "QR code expired. Send /login to try again.",
                    )
            except Exception:
                pass
            _clear_qr_state()
            return

        await asyncio.sleep(3)

        try:
            result = await client(ExportLoginTokenRequest(
                api_id=api_id,
                api_hash=api_hash,
                except_ids=[],
            ))
        except Exception as e:
            log_bot.debug(f"QR poll error: {e}")
            continue

        if isinstance(result, AuthLoginTokenSuccess):
            runtime.user_client = client
            _clear_qr_state()
            log_bot.info("QR login succeeded")

            try:
                if runtime.bot_client:
                    await runtime.bot_client.send_message(
                        owner_id,
                        "✅ Logged in via QR code. You can now use /on, /name, etc.",
                    )
            except Exception:
                pass
            return

        if isinstance(result, AuthLoginTokenMigrateTo):
            try:
                await client.disconnect()
                await client.connect(result.dc_id)
            except Exception as e:
                log_bot.warning(f"QR poll migrate failed: {e}")
            continue

        if isinstance(result, AuthLoginToken):
            token_b64 = base64.urlsafe_b64encode(result.token).rstrip(b"=").decode()
            runtime._qr_token_hex = result.token.hex()
            runtime._qr_expires_at = float(result.expires)
            _save_qr_state(result.token.hex(), result.expires)
            continue