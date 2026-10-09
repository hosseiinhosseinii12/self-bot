"""Optional TOTP-based 2FA for the admin panel."""
import io
from typing import Optional

from .logging_setup import log_flask
from .store import admin_totp_store

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def is_enabled() -> bool:
    return bool(admin_totp_store.get("enabled", False))


def get_secret() -> Optional[str]:
    return admin_totp_store.get("secret")


def provision() -> tuple:
    """Generate (secret, uri, png_bytes)."""
    if not HAS_PYOTP:
        raise RuntimeError("pyotp not installed")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name="selfbot-admin", issuer_name="SELF BOT"
    )
    png = b""
    if HAS_QRCODE:
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    return secret, uri, png


def enable(secret: str) -> None:
    admin_totp_store.set("secret", secret)
    admin_totp_store.set("enabled", True)
    log_flask.info("admin TOTP enabled")


def disable() -> None:
    admin_totp_store.replace({})
    log_flask.info("admin TOTP disabled")


def verify(code: str) -> bool:
    if not is_enabled():
        return True
    if not HAS_PYOTP:
        return False
    secret = get_secret()
    if not secret:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False