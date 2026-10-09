"""Backup and restore: ZIP of all JSON files, sent to owner's Telegram DM."""
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG, DB_PATH
from .logging_setup import log_backup

BACKUP_FILES = [
    "users.json",
    "requests.json",
    "transactions.json",
    "subscriptions.json",
    "config.json",
    "memory.json",
    "user_state.json",
    "user_data.json",
    "templates.json",
    "history.json",
    "shop.json",
    "audit.json",
    "admin_totp.json",
]

BACKUPS_DIR = DB_PATH / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
KEEP_LAST = 7


def _timestamp() -> str:
    return datetime.now(dt_timezone.utc).strftime("%Y%m%d_%H%M%S")


def create_backup_zip() -> Path:
    """Create a ZIP of all backup files. Returns the path."""
    out_path = BACKUPS_DIR / f"backup_{_timestamp()}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in BACKUP_FILES:
            src = DB_PATH / name
            if src.exists():
                zf.write(src, arcname=name)
    _prune_old_backups()
    log_backup.info(f"backup created: {out_path.name}")
    return out_path


def _prune_old_backups() -> None:
    files = sorted(BACKUPS_DIR.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_LAST:]:
        try:
            old.unlink()
        except Exception:
            pass


def send_backup_to_owner(path: Path) -> bool:
    """Send the ZIP to the owner's Telegram DM via the running bot."""
    try:
        from .bootstrap import _async_loop
        from app import _get_runtime
        rt = _get_runtime()
    except Exception as e:
        log_backup.warning(f"cannot get runtime for send_backup: {e}")
        return False

    if rt is None or rt.bot_client is None or _async_loop is None:
        log_backup.warning("bot not connected; backup saved locally only")
        return False

    import asyncio
    owner_id = int(CONFIG.get("owner_id", 338266658))

    async def _send():
        try:
            await rt.bot_client.send_file(
                owner_id,
                str(path),
                caption=f"Daily backup {path.name}",
            )
            return True
        except Exception as e:
            log_backup.error(f"send_backup failed: {e}")
            return False

    try:
        fut = asyncio.run_coroutine_threadsafe(_send(), _async_loop)
        return bool(fut.result(timeout=60))
    except Exception as e:
        log_backup.error(f"send_backup dispatch failed: {e}")
        return False


def create_and_send_backup(runtime) -> Optional[Path]:
    try:
        p = create_backup_zip()
        send_backup_to_owner(p)
        return p
    except Exception as e:
        log_backup.exception(f"create_and_send_backup failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    for member in zf.namelist():
        # prevent path traversal
        if ".." in member or member.startswith("/") or "\\" in member:
            raise ValueError(f"unsafe member: {member}")
        # only allow known JSON files
        if member not in BACKUP_FILES:
            raise ValueError(f"unknown member: {member}")


def restore_from_zip(data: bytes) -> None:
    """Validate and restore all JSON files atomically."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_members(zf)

        # stage in tempdir first
        with tempfile.TemporaryDirectory(dir=str(DB_PATH)) as tmpdir:
            tmp = Path(tmpdir)
            for name in BACKUP_FILES:
                if name in zf.namelist():
                    with zf.open(name) as src, open(tmp / name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                # validate JSON
                if (tmp / name).exists():
                    try:
                        json.loads((tmp / name).read_text(encoding="utf-8"))
                    except Exception as e:
                        raise ValueError(f"{name} is not valid JSON: {e}")

            # move into place atomically
            for name in BACKUP_FILES:
                src = tmp / name
                if not src.exists():
                    continue
                dst = DB_PATH / name
                tmp_dst = dst.with_suffix(dst.suffix + ".restore.tmp")
                shutil.copy2(src, tmp_dst)
                os.replace(tmp_dst, dst)

    log_backup.info("restore completed")