"""Validated environment settings shared by the cache readers and writers."""
import logging
import math
import os

logger = logging.getLogger(__name__)


def number(name, default, minimum=1, maximum=None):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = type(default)(raw)
        if math.isfinite(value) and value >= minimum and (maximum is None or value <= maximum):
            return value
    except (ValueError, OverflowError):
        pass
    logger.warning("Invalid %s setting, using default %s", name, default)
    return default


def ttl(prefix, default_seconds, minimum=1):
    if os.getenv(prefix + "_DAYS", "").strip():
        days = number(prefix + "_DAYS", default_seconds / 86400, 0, 2147483647 / 86400)
        return max(minimum, round(days * 86400))
    return number(prefix + "_SECONDS", default_seconds, minimum, 2147483647)
