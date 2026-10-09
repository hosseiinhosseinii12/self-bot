"""QR Code Login flow for Telegram — per-user sessions."""
import asyncio
import io
import time
from typing import Optional

from .config import CONFIG, DB_PATH, get_api_credentials
from .logging_setup import log_bot
from .store import user_state_store


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


def _render_qr_png(url: str) -> bytes:
    if not HAS_QRCODE:
        return b""
    try:
        from PIL import Image, ImageDraw, ImageFont

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#0a0a0a", back_color="white").convert("RGB")

        qr_w, qr_h = qr_img.size
        pad = 60
        canvas_w = qr_w + pad * 2
        canvas_h = qr_h + pad * 2 + 90

        canvas = Image.new("RGB", (canvas_w, canvas_h), "#fafafa")
        draw = ImageDraw.Draw(canvas)

        accent_colors = ["#0a0a0a", "#525252", "#a3a3a3"]
        for i, col in enumerate(accent_colors):
            y = 22 + i * 7
            draw.rectangle([24, y, 24 + 70, y + 4], fill=col)

        card_pad = 14
        card_box = [
            pad - card_pad, pad - card_pad,
            pad + qr_w + card_pad, pad + qr_h + card_pad,
        ]
        try:
            draw.rounded_rectangle(card_box, radius=18, fill="white",
                                    outline="#e5e5e5", width=1)
        except AttributeError:
            draw.rectangle(card_box, fill="white", outline="#e5e5e5")

        canvas.paste(qr_img, (pad, pad))

        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        except Exception:
            font = ImageFont.load_default()

        caption = "Scan with Telegram"
        try:
            bbox = draw.textbbox((0, 0), caption, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(caption) * 8
        draw.text(((canvas_w - tw) // 2, pad + qr_h + 40),
                  caption, fill="#525252", font=font)

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    except Exception as e:
        log_bot.warning(f"styled qr failed, falling back: {e}")
        try:
            qr = qrcode.QRCode(box_size=10, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e2:
            log_bot.error(f"plain qr failed: {e2}")
            return b""


def _save_qr_state(uid: int, expires_at: float) -> None:
    try:
        user_state_store.set(f"qr_login:{uid}", {"expires_at": expires_at})
    except Exception:
        pass


def _clear_qr_state(uid: int) -> None:
    try:
        user_state_store.delete(f"qr_login:{uid}")
    except Exception:
        pass


async def start_qr_login(runtime, user_id: int = None) -> tuple:
    """Start QR login for a user. Returns (ok, msg_or_url, png, expires_at)."""
    if not HAS_TELETHON:
        return False, f"Telethon import failed: {TELETHON_ERROR or 'unknown'}", b"", 0
    if not HAS_QRCODE:
        return False, f"qrcode import failed: {QRCODE_ERROR or 'not installed'}", b"", 0

    uid = int(user_id) if user_id else None

    client = runtime.user_clients.get(uid) if uid else None
    fresh = False
    if client is None:
        try:
            from .bootstrap import make_user_client_for_login
            client = await make_user_client_for_login(uid)
            if uid:
                runtime.user_clients[uid] = client
            fresh = True
        except Exception as e:
            log_bot.error(f"qr: failed to create user client: {e}")
            return False, f"Failed to create client: {type(e).__name__}: {e}", b"", 0

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

    log_bot.info(f"qr: got url for uid={uid} (len={len(qr_url)})")
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
    if uid:
        _save_qr_state(uid, expires_at)

    asyncio.create_task(_wait_native(runtime, qr_obj, uid))

    log_bot.info(f"qr: started for uid={uid} (expires_at={expires_at})")
    return True, qr_url, png, expires_at


async def _wait_native(runtime, qr_obj, user_id: int = None) -> None:
    uid = int(user_id) if user_id else int(CONFIG.get("owner_id", 338266658))
    client = runtime._qr_client

    try:
        await asyncio.wait_for(qr_obj.wait(), timeout=90.0)
    except asyncio.TimeoutError:
        await _notify(runtime, uid, "⏱ QR code expired. Try again.")
        _clear_qr_state(uid)
        return
    except SessionPasswordNeededError:
        await _notify(runtime, uid,
                      "✅ QR scanned. 🔐 2FA required.\nSend your 2FA password now.")
        runtime._qr_needs_2fa = True
        st = runtime.state_for(uid)
        st.setdefault("awaiting", {})["qr_2fa_password"] = True
        runtime.save_state()
        return
    except Exception as e:
        log_bot.error(f"qr wait_native failed: {type(e).__name__}: {e}")
        await _notify(runtime, uid, f"❌ QR login failed: {e}")
        _clear_qr_state(uid)
        return

    if user_id:
        runtime.user_clients[int(user_id)] = client
    _clear_qr_state(uid)
    log_bot.info(f"qr: login succeeded for uid={uid}")

    try:
        authorized = await client.is_user_authorized()
        log_bot.info(f"qr: is_user_authorized={authorized} for uid={uid}")
    except Exception:
        pass

    await _notify(runtime, uid, "✅ Logged in via QR code.")
    try:
        from .users import get_user_settings
        s = get_user_settings(uid)
        if s.get("clock_on"):
            from .clock import start_clock
            start_clock(client, uid)
    except Exception:
        pass


async def finish_qr_2fa(runtime, user_id: int, password: str) -> tuple:
    uid = int(user_id)
    client = runtime.user_clients.get(uid)
    if client is None:
        client = getattr(runtime, "_qr_client", None)
    if client is None:
        return False, "No pending QR login."
    try:
        await client.sign_in(password=password)
    except Exception as e:
        return False, f"2FA failed: {type(e).__name__}: {e}"
    runtime.user_clients[uid] = client
    runtime._qr_needs_2fa = False
    _clear_qr_state(uid)
    try:
        from .users import get_user_settings
        s = get_user_settings(uid)
        if s.get("clock_on"):
            from .clock import start_clock
            start_clock(client, uid)
    except Exception:
        pass
    return True, "OK"


async def _notify(runtime, owner_id: int, text: str) -> None:
    try:
        if runtime.bot_client:
            await runtime.bot_client.send_message(owner_id, text)
    except Exception:
        pass