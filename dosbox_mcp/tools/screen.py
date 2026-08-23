# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import base64
import json

from ..connection import to_error_result

# Mirrors the engine's own num_param bound on crop_x/crop_y/crop_w/
# crop_h (video.cpp's GetFrame) - duplicated here for the same reason
# as every other engine-constant mirror in this bridge (schemas are
# built once at startup, before there's a live connection to read the
# real bound from). Without this, an out-of-range crop coordinate would
# pass schema validation and only get rejected by the engine afterward.
MAX_CROP_COORDINATE = 65535


def register(server, client, add_tool, feature=None):
    add_tool(
        name="screen_text",
        description=(
            "Read the DOS text-mode screen buffer as a string. "
            "Works only in text modes (CGA/EGA/VGA/Hercules text)."
        ),
        risk="read",
        title="Read Screen Text",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _screen_text(client),
    )

    add_tool(
        name="screen_capture",
        description=(
            "Capture the current screen as an image. Works in all video "
            "modes. 'format' is 'png' (default, lossless) or 'jpeg' - "
            "opt into jpeg only for large graphics frames where the "
            "smaller size is worth it; jpeg's chroma subsampling smears "
            "text-mode glyphs and CGA/EGA dithering, exactly the detail "
            "usually needed. 'mode' picks which framebuffer: 'raw' "
            "(default) is the native DOS-mode buffer at its own "
            "resolution, before any scaler or shader; 'rendered' is "
            "what a human looking at the window actually sees right "
            "now, at the window's own resolution. 'scale' downsamples "
            "by an integer divisor (box filter) for a cheap thumbnail. "
            "'crop' takes a sub-rectangle in the frame's own pixel "
            "coordinates - crop_x/crop_y/crop_w/crop_h must all be "
            "given together, and are rejected (not clamped) if the "
            "rectangle doesn't fit the frame - check screen_info for "
            "the frame's real width/height first."
        ),
        risk="read",
        title="Capture Screen",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["raw", "rendered"],
                    "description": (
                        "Which framebuffer to capture (default 'raw')."
                    ),
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                    "description": "Image format (default 'png').",
                },
                "quality": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": (
                        "jpeg quality 1-100 (default 98). Ignored for png."
                    ),
                },
                "png_level": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 9,
                    "description": (
                        "PNG zlib compression level 0-9 (default 6, "
                        "higher is smaller but slower). Ignored for jpeg."
                    ),
                },
                "scale": {
                    "type": "integer",
                    # Mirrors the engine's own fixed divisor check
                    # (video.cpp's GetFrame) - duplicated here for the
                    # same reason as every other engine-constant mirror
                    # in this bridge: schemas are built once at startup,
                    # before there's a live connection to read the real
                    # set from. Keep in sync by hand.
                    "enum": [1, 2, 4, 8],
                    "description": (
                        "Downscale by this divisor with a box filter "
                        "(default 1, no scaling)."
                    ),
                },
                "crop_x": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_CROP_COORDINATE,
                    "description": "Crop rectangle's left edge, in frame pixels.",
                },
                "crop_y": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_CROP_COORDINATE,
                    "description": "Crop rectangle's top edge, in frame pixels.",
                },
                "crop_w": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_CROP_COORDINATE,
                    "description": "Crop rectangle's width, in frame pixels.",
                },
                "crop_h": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_CROP_COORDINATE,
                    "description": "Crop rectangle's height, in frame pixels.",
                },
            },
            "dependentRequired": {
                "crop_x": ["crop_y", "crop_w", "crop_h"],
                "crop_y": ["crop_x", "crop_w", "crop_h"],
                "crop_w": ["crop_x", "crop_y", "crop_h"],
                "crop_h": ["crop_x", "crop_y", "crop_w"],
            },
        },
        handler=lambda args: _screen_capture(client, args),
    )

    add_tool(
        name="screen_info",
        description="Frame metadata: resolution, pixel format, palette status.",
        risk="read",
        title="Screen Info",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _screen_info(client),
    )


def _screen_text(client):
    import mcp.types as types

    result = client.get("/api/v1/video/text")

    cursor_row = result.get("cursor_row", 0)
    cursor_col = result.get("cursor_col", 0)
    bios_mode = result.get("bios_mode", 0)

    if not result.get("is_text_mode", False):
        text = (
            f"Not in a text mode (BIOS mode 0x{bios_mode:02x}). "
            f"Cursor at {cursor_row},{cursor_col}."
        )
        return [types.TextContent(type="text", text=text)]

    # Real text, not an escaped JSON string: an 80x25 screen is one
    # ~2050-character line of literal \n sequences and mostly trailing
    # spaces when json.dumps()'d, re-paid on every poll for no benefit -
    # an agent reads a grid, not an escaped blob.
    rows = [row.rstrip() for row in result.get("text", "").split("\n")]
    # dos_to_utf8's row-separating newlines include one after the last
    # row, so splitting yields one more element than the engine's own
    # 'rows' count; bound to that count rather than reporting a phantom
    # extra blank row.
    row_count = result.get("rows")
    if row_count:
        rows = rows[:row_count]

    trailing_blank = 0
    while rows and not rows[-1]:
        rows.pop()
        trailing_blank += 1

    width = len(str(len(rows) - 1)) if rows else 1
    body = [f"{i:>{width}} {row}" for i, row in enumerate(rows)]

    header = (
        f"{result.get('columns', 0)}x{result.get('rows', 0)} "
        f"mode 0x{bios_mode:02x} cursor {cursor_row},{cursor_col} "
        f"hash 0x{result.get('text_hash', '0' * 16)}"
    )
    if trailing_blank:
        plural = "s" if trailing_blank != 1 else ""
        header += f" ({trailing_blank} blank row{plural} omitted)"

    text = "\n".join([header, *body]) if body else header
    return [types.TextContent(type="text", text=text)]


_CROP_FIELDS = ("crop_x", "crop_y", "crop_w", "crop_h")


def _screen_capture(client, args):
    import mcp.types as types

    fmt = args.get("format", "png")
    if fmt not in ("png", "jpeg"):
        # The schema's enum already rejects anything else before this
        # handler ever runs - this is a second, defense-in-depth check
        # specifically because the returned mimeType below is a claim
        # about the bytes' real format, not just a shape mismatch: a
        # caller that reached this with format='raw' (only possible by
        # bypassing schema validation entirely) must not get raw pixel
        # bytes back labeled image/jpeg.
        return to_error_result(
            f"format must be 'png' or 'jpeg', got {fmt!r}",
            tool="screen_capture", code="invalid_argument",
        )
    params = {"format": fmt}

    if "mode" in args:
        params["mode"] = args["mode"]
    if fmt == "jpeg" and "quality" in args:
        params["quality"] = args["quality"]
    if fmt == "png" and "png_level" in args:
        params["png_level"] = args["png_level"]
    if "scale" in args:
        params["scale"] = args["scale"]
    for field in _CROP_FIELDS:
        if field in args:
            params[field] = args[field]

    data = client.get("/api/v1/video/frame", params=params)
    encoded = base64.b64encode(data).decode("ascii")
    mime_type = "image/png" if fmt == "png" else "image/jpeg"
    return [types.ImageContent(type="image", data=encoded, mimeType=mime_type)]


def _screen_info(client):
    import mcp.types as types
    result = client.get("/api/v1/video/frame/info")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
