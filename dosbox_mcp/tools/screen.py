# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import base64
import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="screen_text",
        description=(
            "Read the DOS text-mode screen buffer as a string. "
            "Works only in text modes (CGA/EGA/VGA/Hercules text)."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _screen_text(client),
    )

    add_tool(
        name="screen_capture",
        description=(
            "Capture the current screen as a PNG image. Works in all video modes."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _screen_capture(client),
    )

    add_tool(
        name="screen_info",
        description="Frame metadata: resolution, pixel format, palette status.",
        read_only=True,
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


def _screen_capture(client):
    import mcp.types as types
    data = client.get("/api/v1/video/frame", params={"format": "png"})
    encoded = base64.b64encode(data).decode("ascii")
    return [types.ImageContent(type="image", data=encoded, mimeType="image/png")]


def _screen_info(client):
    import mcp.types as types
    result = client.get("/api/v1/video/frame/info")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
