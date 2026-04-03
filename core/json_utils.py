"""
Shared JSON serialization utilities.
Prevents TypeError when Flask's jsonify encounters None keys or values.
"""

from json import JSONEncoder
from typing import Any


class SafeJSONEncoder(JSONEncoder):
    """Custom JSON encoder that safely handles None values and nested structures."""

    def default(self, obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return {str(k): self.default(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, (list, tuple)):
            return [self.default(item) for item in obj if item is not None]
        return super().default(obj)


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize a data structure to remove None values and ensure
    JSON compatibility.  Prevents TypeError when jsonify tries to sort keys
    containing None values.
    """
    if obj is None:
        return None

    if isinstance(obj, dict):
        sanitized = {}
        for key, value in obj.items():
            if value is not None:
                sanitized_key = str(key) if not isinstance(key, str) else key
                sanitized[sanitized_key] = sanitize_for_json(value)
        return sanitized

    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(item) for item in obj if item is not None]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    return str(obj)
