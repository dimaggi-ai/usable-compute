"""One strict JSON boundary and deterministic local identity encoding."""
from __future__ import annotations

import hashlib
import json

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64


def _pairs(items):
    result = {}
    for key, value in items:
        validate_unicode(key)
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text):
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("JSON input exceeds 4 MiB")
    def invalid(value):
        raise ValueError(f"non-finite JSON constant: {value}")
    try:
        value = json.loads(text, parse_constant=invalid, object_pairs_hook=_pairs)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds 64 levels") from exc
    pending = [(value, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise ValueError("JSON nesting exceeds 64 levels")
        if isinstance(node, dict):
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
    # JSON's exponent syntax can overflow to infinity without parse_constant.
    canonical(value)
    return value


def canonical(value):
    validate_unicode(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def validate_unicode(value):
    """Reject surrogate code points instead of letting transports replace identities.

    JSON escaped *paired* surrogates decode to a single Unicode scalar and pass.
    Inspect keys as well as values and direct Python callers as well as JSON input.
    Circular-container errors remain the JSON encoder's responsibility.
    """
    pending = [value]
    seen = set()
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in node):
                raise ValueError("JSON strings must contain only Unicode scalar values")
        elif isinstance(node, (dict, list, tuple)) and id(node) not in seen:
            seen.add(id(node))
            if isinstance(node, dict):
                pending.extend(node.keys())
                pending.extend(node.values())
            else:
                pending.extend(node)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def dumps(value):
    validate_unicode(value)
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
