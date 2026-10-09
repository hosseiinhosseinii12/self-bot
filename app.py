#!/usr/bin/env python3
"""
SELF BOT — entrypoint.
Boots logging, config, stores, bots, Flask (admin + user), scheduler.
Run: python app.py   |   Prod: gunicorn --bind 0.0.0.0:$PORT app:flask_app
"""
import os
import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from selfbot.config import CONFIG, DB_PATH, release_lock           # noqa: E402
from selfbot.logging_setup import log, log_bot, log_flask           # noqa: E402
from selfbot.bootstrap import boot_all, shutdown_all                # noqa: E402
from selfbot.admin_panel import build_admin_app                     # noqa: E402
from selfbot.user_panel import build_user_app                       # noqa: E402
from selfbot.api import attach_api_routes                           # noqa: E402
from selfbot.bot_handlers import build_bot_runtime                  # noqa: E402


# ---------------------------------------------------------------------------
# File lock (fcntl) — safe for Gunicorn multi-worker
# ---------------------------------------------------------------------------
_lock_fh = None
_HAS_LOCK = False

try:
    import fcntl
    _lock_fh = open(DB_PATH / "lock", "w")
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
    _HAS_LOCK = True
except (BlockingIOError, OSError):
    _HAS_LOCK = False
    if _lock_fh:
        try:
            _lock_fh.close()
        except Exception:
            pass
        _lock_fh = None
except Exception:
    _HAS_LOCK = False


# ---------------------------------------------------------------------------
# Runtime singleton
# ---------------------------------------------------------------------------
_runtime = None
_runtime_lock = threading.Lock()


def _get_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = build_bot_runtime()
        return _runtime


# ---------------------------------------------------------------------------
# Flask app (admin + user + api)
# ---------------------------------------------------------------------------
admin_app = build_admin_app()
user_app_bp = build_user_app()

flask_app = admin_app
attach_api_routes(flask_app)
flask_app.register_blueprint(user_app_bp)


# ---------------------------------------------------------------------------
# Boot async bots in a background thread
# ---------------------------------------------------------------------------
def _boot_bots():
    try:
        boot_all(_get_runtime())
    except Exception as e:
        log_bot.exception(f"boot failed: {e}")


_boot_started_lock = threading.Lock()
_boot_started = False


def _ensure_started():
    global _boot_started
    with _boot_started_lock:
        if _boot_started:
            return
        _boot_started = True
    t = threading.Thread(target=_boot_bots, daemon=True, name="selfbot-boot")
    t.start()


if _HAS_LOCK:
    _ensure_started()
else:
    # Another worker owns the bot; this one only serves Flask.
    pass


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
import atexit  # noqa: E402

@atexit.register
def _on_exit():
    try:
        shutdown_all(_get_runtime())
    except Exception as e:
        log.error(f"shutdown error: {e}")
    try:
        if _lock_fh:
            import fcntl
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
    except Exception:
        pass
    try:
        release_lock()
    except Exception:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    log_flask.info(f"starting admin panel on 0.0.0.0:{port}")
    try:
        flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        _on_exit()