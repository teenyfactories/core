"""Time utilities for teenyfactories.

Two flavours of "now" are exposed:

    tf.get_timestamp()      — local time per the $TZ env var (UTC fallback)
    tf.get_timestamp_utc()  — UTC

Both return an ISO-8601 string with timezone offset. The local flavour
honours the standard POSIX `TZ` environment variable set by the
orchestrator on each agent container, so timestamps match the deployment
locale without any factory-side configuration.
"""

import os
import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _local_tz() -> datetime.tzinfo:
    """Resolve the local tz from $TZ. Falls back to the system tz, then UTC."""
    name = os.getenv('TZ')
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    system = datetime.datetime.now().astimezone().tzinfo
    return system or datetime.timezone.utc


def get_timestamp() -> str:
    """
    Local-time ISO-8601 string honouring `$TZ`.

    Example (TZ=Australia/Sydney):
        >>> get_timestamp()
        '2025-11-04T15:30:45.123456+11:00'
    """
    return datetime.datetime.now(_local_tz()).isoformat()


def get_timestamp_utc() -> str:
    """
    UTC ISO-8601 string.

    Example:
        >>> get_timestamp_utc()
        '2025-11-04T04:30:45.123456+00:00'
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
