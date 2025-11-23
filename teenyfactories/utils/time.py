"""Time and timezone utilities for teenyfactories"""

import datetime
from zoneinfo import ZoneInfo

# AEST Timezone constant
AEST_TIMEZONE = ZoneInfo('Australia/Sydney')


def get_aest_now():
    """
    Get current datetime in AEST timezone

    Returns:
        datetime.datetime: Current time in AEST (Australia/Sydney) timezone

    Example:
        >>> now = get_aest_now()
        >>> print(now.tzinfo)
        Australia/Sydney
    """
    return datetime.datetime.now(AEST_TIMEZONE)


def get_timestamp():
    """
    Get current timestamp in ISO format

    Returns:
        str: ISO 8601 formatted timestamp with AEST timezone
             Example: "2025-11-04T15:30:45.123456+11:00"

    Example:
        >>> timestamp = get_timestamp()
        >>> print(timestamp)
        2025-11-04T15:30:45.123456+11:00
    """
    return get_aest_now().isoformat()
