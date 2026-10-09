"""Configuration: env -> config.json -> defaults. Lock file handling."""
import json
import os
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data"))).resolve()
DB_PATH.mkdir(parents=True, exist_ok=True)
(DB_PATH / "logs").mkdir(exist_ok=True)
(DB_PATH / "backups").mkdir(exist_ok=True)

CONFIG_FILE = DB_PATH / "config.json"
LOCK_FILE = DB_PATH / "lock"

PUBLIC_API_ID = 2040
PUBLIC_API_HASH = "b18441a1ff607e10a989891a5462e627"
ANDROID_API_ID = 6
ANDROID_API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"

DEFAULTS = {
    "api_id": PUBLIC_API_ID,
    "api_hash": PUBLIC_API_HASH,
    "bot_token": "",
    "owner_id": 338266658,
    "cf_worker_domain": "autumn-dust-5fb8.v2config.workers.dev",
    "admin_password": "",
    "port": 8080,
    "signup_bonus": 10,
    "diamond_cost_clock": 1,
    "diamond_cost_job": 5,
    "referral_bonus": 5,
    "api_token": "",
    "use_proxy": True,
    "proxy_url": "socks5://127.0.0.1:1080",
    "language": "en",
    "timezone": "UTC",
    "interval": 5,
    "base_name": "User",
    "name_font": "normal",
    "clock_font": "double",
    "custom_name_font": "",
    "custom_clock_font": "",
    "clock_on": False,
}


def _coerce(default: Any, value: str) -> Any:
    if isinstance(default, bool):
        return str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    for key, default in DEFAULTS.items():
        env_val = os.environ.get(key.upper())
        if env_val not in (None, ""):
            cfg[key] = _coerce(default, env_val)
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


CONFIG = load_config()


def acquire_lock() -> None:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    print(f"[FATAL] Another instance running (PID {pid}). Exiting.")
                    sys.exit(1)
                except OSError:
                    pass
        except Exception:
            pass
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def get_api_credentials() -> tuple:
    api_id = CONFIG.get("api_id") or PUBLIC_API_ID
    api_hash = CONFIG.get("api_hash") or PUBLIC_API_HASH
    return int(api_id), str(api_hash)


def android_fallback() -> tuple:
    return ANDROID_API_ID, ANDROID_API_HASH


def get_proxy_test_url() -> str:
    return "https://api.ipify.org"