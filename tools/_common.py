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


def with_preview(result):
    """If the result carries a preview PNG, return [json, Image] so Claude can
    actually look at it; otherwise plain json text."""
    png = result.pop("preview_png_base64", None)
    text = as_json(result)
    if png:
        return [text, Image(data=base64.b64decode(png), format="png")]
    return text
