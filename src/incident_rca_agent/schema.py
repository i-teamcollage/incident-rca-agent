"""Check model output against the small part of JSON Schema the agents use.

Error messages are written here, not by a library, so they never change between versions;
they are part of the retry prompt, and the retry prompt is part of the stored run.
"""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(data: Any, schema: dict[str, Any]) -> Any:
    """Return data unchanged if it matches the schema; otherwise raise ValueError naming the path."""
    _check(data, schema, "")
    return data


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    where = path or "answer"
    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not any(_TYPES[name](value) for name in allowed):
            raise ValueError(f"{where}: expected {' or '.join(allowed)}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where}: expected one of {schema['enum']}, got {value!r}")
    if isinstance(value, dict) and "properties" in schema:
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{_join(path, key)}: missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in schema["properties"]:
                    raise ValueError(f"{where}: unexpected key {key!r}")
        for key, child in schema["properties"].items():
            if key in value:
                _check(value[key], child, _join(path, key))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{index}]")


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
