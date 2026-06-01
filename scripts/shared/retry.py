"""
Shared retry utilities for external API calls.
"""

import functools
import time
import logging

logger = logging.getLogger(__name__)


def retry(
    max_retries: int = 3,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """Retry decorator for general exceptions."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries - 1:
                        sleep_time = backoff ** attempt
                        logger.warning(
                            "retry %s: attempt %s/%s, sleeping %.1fs — %s",
                            fn.__name__, attempt + 1, max_retries, sleep_time, exc,
                        )
                        time.sleep(sleep_time)
            raise last_exc

        return wrapper

    return decorator


def retry_http(
    max_retries: int = 3,
    backoff: float = 2.0,
    retryable_status: tuple = (429, 500, 502, 503, 504),
):
    """
    Retry decorator for functions that return requests.Response-like objects,
    or that may raise ConnectionError / Timeout.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    resp = fn(*args, **kwargs)
                    # Validate HTTP status if response-like
                    if hasattr(resp, "status_code"):
                        if not resp.ok:
                            txt = getattr(resp, "text", "")[:200]
                            if resp.status_code in retryable_status and attempt < max_retries - 1:
                                sleep_time = backoff ** attempt
                                logger.warning(
                                    "retry_http %s: HTTP %s, sleeping %.1fs — %s",
                                    fn.__name__, resp.status_code, sleep_time, txt,
                                )
                                time.sleep(sleep_time)
                                continue
                        return resp
                    return resp
                except Exception as exc:
                    last_exc = exc
                    if attempt < max_retries - 1:
                        sleep_time = backoff ** attempt
                        logger.warning(
                            "retry_http %s: attempt %s/%s, sleeping %.1fs — %s",
                            fn.__name__, attempt + 1, max_retries, sleep_time, exc,
                        )
                        time.sleep(sleep_time)
            if last_exc:
                raise last_exc
            return None

        return wrapper

    return decorator
