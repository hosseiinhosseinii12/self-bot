"""Structured logging via QueueHandler/QueueListener -> data/logs/{app,error}.log."""
import atexit
import logging
import logging.handlers
import queue
import sys

from .config import DB_PATH

_log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue(-1)


# ---------------------------------------------------------------------------
# Ensure EVERY LogRecord has a 'service' attribute, even ones created by Flask
# ---------------------------------------------------------------------------
_original_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    if not hasattr(record, "service"):
        record.service = "self-bot"
    return record


logging.setLogRecordFactory(_record_factory)


class ServiceAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("service", self.extra.get("service", "self-bot"))
        return f"[{extra['service']}] {msg}", kwargs


def _build_root() -> logging.Logger:
    root = logging.getLogger("selfbot")
    if root.handlers:
        return root
    root.setLevel(logging.INFO)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(service)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app_h = logging.handlers.RotatingFileHandler(
        DB_PATH / "logs" / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_h.setFormatter(fmt)

    err_h = logging.handlers.RotatingFileHandler(
        DB_PATH / "logs" / "error.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(fmt)

    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(fmt)

    listener = logging.handlers.QueueListener(
        _log_queue, app_h, err_h, console_h, respect_handler_level=True
    )
    listener.start()
    atexit.register(listener.stop)

    qh = logging.handlers.QueueHandler(_log_queue)
    root.addHandler(qh)
    return root


_BASE_LOG = _build_root()

log = ServiceAdapter(_BASE_LOG, {"service": "self-bot"})
log_bot = ServiceAdapter(_BASE_LOG, {"service": "telegram"})
log_flask = ServiceAdapter(_BASE_LOG, {"service": "flask"})
log_clock = ServiceAdapter(_BASE_LOG, {"service": "clock"})
log_jobs = ServiceAdapter(_BASE_LOG, {"service": "jobs"})
log_econ = ServiceAdapter(_BASE_LOG, {"service": "economy"})
log_backup = ServiceAdapter(_BASE_LOG, {"service": "backup"})