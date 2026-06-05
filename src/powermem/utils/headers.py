import json
import re
from collections.abc import Mapping
from typing import Dict, Optional


def parse_default_headers(value) -> Optional[Dict[str, str]]:
    """Parse provider default headers from a dict, JSON object, or `Name: value` list."""
    if value is None:
        return None

    if isinstance(value, Mapping):
        return {str(key): str(val) for key, val in value.items() if val is not None}

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            return parse_default_headers(decoded)
        if decoded is not None:
            raise ValueError("default_headers JSON value must be an object")

        headers: Dict[str, str] = {}
        for item in re.split(r"[\n;,]+", raw):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError("default_headers entries must use 'Header-Name: value'")
            name, header_value = item.split(":", 1)
            name = name.strip()
            header_value = header_value.strip()
            if not name:
                raise ValueError("default_headers header names cannot be empty")
            headers[name] = header_value
        return headers or None

    raise ValueError("default_headers must be a mapping or string")
