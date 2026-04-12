"""ID generation utilities for teenyfactories"""

import uuid


def generate_unique_id():
    """
    Generate a unique ID for records

    Returns:
        str: UUID4 string
             Example: "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    Example:
        >>> record_id = generate_unique_id()
        >>> print(len(record_id))
        36
    """
    return str(uuid.uuid4())
