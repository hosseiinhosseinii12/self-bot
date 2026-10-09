"""Retry helpers using backoff. Wrap all external HTTP calls with these."""
from typing import Callable

try:
    import backoff
    HAS_BACKOFF = True
except ImportError:
    HAS_BACKOFF = False

from .logging_setup import log


def _on_giveup(details: dict) -> None:
    log.error(
        f"retry giveup: target={details.get('target')} "
        f"args={details.get('args')} kwargs={details.get('kwargs')} "
        f"elapsed={details.get('elapsed', 0):.2f}s"
    )


if HAS_BACKOFF:
    def http_retry(max_tries: int = 5, max_time: float = 30.0,
                   exceptions: tuple = (Exception,)):
        """Decorator: exponential backoff with full jitter."""
        return backoff.on_exception(
            backoff.expo,
            exceptions,
            max_tries=max_tries,
            max_time=max_time,
            jitter=backoff.full_jitter,
            on_giveup=_on_giveup,
        )
else:
    def http_retry(max_tries: int = 5, max_time: float = 30.0,
                   exceptions: tuple = (Exception,)):
        def deco(fn: Callable):
            return fn
        return deco


def retry_call(fn: Callable, *args, **kwargs):
    """Call fn with backoff; fallback: single try."""
    if HAS_BACKOFF:
        wrapped = backoff.on_exception(
            backoff.expo, Exception,
            max_tries=5, max_time=30.0, jitter=backoff.full_jitter,
            on_giveup=_on_giveup,
        )(fn)
        return wrapped(*args, **kwargs)
    return fn(*args, **kwargs)