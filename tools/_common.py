"""Shared helpers for tool modules."""
import base64
import json

from mcp.server.fastmcp import Image

TARGET_DOC = (
    "Target: 'selection' (whatever is selected in the ChemDraw window), "
    "'document' (everything on the page), a single object_id string, or a "
    "JSON list of object_ids."
)


def as_json(data):
    return json.dumps(data, indent=2, default=str)


def parse_json_arg(json_str, name, kind):
    """Parse a *_json tool argument and verify its top-level shape.

    A syntactically valid but wrong-typed value (e.g. rotate_ids_json="123"
    parses fine to the int 123, not a list) used to pass this boundary
    silently and only fail later with a confusing low-level error deep in
    bridge.py. Raising here, with the parameter name and the offending
    text, puts the error at the point the caller actually made the
    mistake."""
    try:
        value = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, kind):
        label = {list: "a JSON list", dict: "a JSON object"}.get(kind, kind.__name__)
        raise ValueError(
            f"{name} must be {label}, got {type(value).__name__}: {json_str!r}"
        )
    return value


def parse_optional_json_arg(json_str, name, kind):
    """Same as parse_json_arg, but a blank/whitespace-only string means
    'not given' -> None, matching every optional *_json parameter's
    existing convention."""
    if not json_str or not json_str.strip():
        return None
    return parse_json_arg(json_str, name, kind)


def with_preview(result):
    """If the result carries a preview PNG, return [json, Image] so Claude can
    actually look at it; otherwise plain json text."""
    png = result.pop("preview_png_base64", None)
    text = as_json(result)
    if png:
        return [text, Image(data=base64.b64decode(png), format="png")]
    return text
