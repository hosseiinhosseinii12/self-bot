"""Atomic, thread-safe JSON stores."""
import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import DB_PATH


class PersistentStore:
    def __init__(self, filename: str, default: Any):
        self.path = DB_PATH / filename
        self._lock = threading.RLock()
        self._default = default
        self.data = default
        self._load()

    def _load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    self.data = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    self.data = self._default
            else:
                self.data = self._default
                self._save_locked()

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def replace(self, new_data: Any) -> None:
        with self._lock:
            self.data = new_data
            self._save_locked()

    def get(self, key, default=None):
        with self._lock:
            if isinstance(self.data, dict):
                return self.data.get(key, default)
            return default

    def set(self, key, value) -> None:
        with self._lock:
            if not isinstance(self.data, dict):
                self.data = {}
            self.data[key] = value
            self._save_locked()

    def update(self, mapping: dict) -> None:
        with self._lock:
            if not isinstance(self.data, dict):
                self.data = {}
            self.data.update(mapping)
            self._save_locked()

    def delete(self, key) -> None:
        with self._lock:
            if isinstance(self.data, dict) and key in self.data:
                del self.data[key]
                self._save_locked()

    def all(self):
        with self._lock:
            return self.data


# --- shared stores ---
memory_store = PersistentStore("memory.json", {})
user_state_store = PersistentStore("user_state.json", {})
user_data_store = PersistentStore("user_data.json", {})
users_store = PersistentStore("users.json", {})
requests_store = PersistentStore("requests.json", [])
transactions_store = PersistentStore("transactions.json", [])
subscriptions_store = PersistentStore("subscriptions.json", {})
templates_store = PersistentStore("templates.json", {})
history_store = PersistentStore("history.json", [])
shop_store = PersistentStore("shop.json", {})
audit_store = PersistentStore("audit.json", [])
admin_totp_store = PersistentStore("admin_totp.json", {})