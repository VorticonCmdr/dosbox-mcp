# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="video_capture_start",
        description=(
            "Start ZMBV video recording of the emulator screen. "
            "'mode' selects what feeds the encoder: 'raw' (default) is "
            "the emulator framebuffer at native resolution; 'rendered' "
            "is the post-shader output at window resolution as shown "
            "on screen. 'compression' (0-9, store-only to maximum) is "
            "set for that mode and the recording started atomically in "
            "this one call - it is refused with a 409 if a capture is "
            "already running, since the zlib level is latched at start "
            "and a mid-recording change would silently not apply."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["raw", "rendered"],
                    "description": "What feeds the encoder (default 'raw').",
                },
                "compression": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 9,
                    "description": "Zlib level for `mode`, applied before starting.",
                },
            },
        },
        handler=lambda args: _capture_start(client, args),
    )

    add_tool(
        name="video_capture_stop",
        description="Stop ZMBV video recording.",
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _capture_stop(client),
    )

    add_tool(
        name="video_capture_status",
        description=(
            "Video capture state: whether it's recording, the mode, "
            "the host filesystem path, frames written, elapsed_ms "
            "(measured from when the file was actually created, not "
            "from the start call), bytes_written, the configured "
            "compression_level, and why the last recording ended. "
            "path/frames/elapsed_ms/bytes_written keep reporting the "
            "last recording's final values after it stops - checking "
            "right after stopping is the normal sequence. frames "
            "staying at 0 while capturing is true is the tell that "
            "nothing was actually written (e.g. the emulator was "
            "paused the whole time)."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _capture_status(client),
    )


def _capture_start(client, args):
    import mcp.types as types
    body = {}
    if "mode" in args:
        body["mode"] = args["mode"]
    if "compression" in args:
        body["compression"] = args["compression"]
    result = client.post("/api/v1/capture/video/start", json=body) if body \
        else client.post("/api/v1/capture/video/start")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _capture_stop(client):
    import mcp.types as types
    result = client.post("/api/v1/capture/video/stop")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _capture_status(client):
    import mcp.types as types
    result = client.get("/api/v1/capture/video/status")
    return [types.TextContent(type="text", text=json.dumps(result))]


def register_drive(server, client, add_tool, feature=None):
    add_tool(
        name="drive_list",
        description=(
            "List drive letters A-Z and what's mounted on each: type "
            "(local/cdrom/fat/iso/virtual), the mounted host path, "
            "read-only, and removable. Unmounted letters get just "
            "{letter, mounted:false} - use this to find a free letter "
            "before drive_swap, or to confirm what a multi-disk "
            "installer is currently reading from."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _drive_list(client),
        feature=feature,
    )

    add_tool(
        name="mount_status",
        description=(
            "Whether mounting is locked (mount_lock) and the "
            "directory/image roots an API-origin mount must be under. "
            "An empty allowed_image_roots means every drive_swap call "
            "is refused by policy regardless of path - the "
            "out-of-the-box state until an operator configures "
            "mount_allowed_image_roots in the primary config. Check "
            "this first if drive_swap keeps returning "
            "outside_whitelist."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _mount_status(client),
        feature=feature,
    )

    add_tool(
        name="mount_images",
        description=(
            "List image files under the configured image roots "
            "(mount_status's allowed_image_roots), grouped by root - "
            "so an agent can discover what's mountable without shell "
            "access. Each entry's path is independently re-validated "
            "against the same policy drive_swap enforces, so a path "
            "returned here will not then be refused by mount policy "
            "(it can still fail structural validation as a disk "
            "image - drive_swap's own not_a_disk_image error). "
            "Non-recursive per root; 'truncated' on a root means it "
            "held more files than the engine's per-root cap."
        ),
        read_only=True,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _mount_images(client),
        feature=feature,
    )

    add_tool(
        name="drive_swap",
        description=(
            "Mount or swap a floppy or hard disk image on a drive "
            "letter. For multi-disk installs, call this when the "
            "installer prompts for the next disk. The image path must "
            "resolve under one of mount_status's allowed_image_roots - "
            "an operator-configured whitelist, not something this "
            "call can bypass - and mounting must not be locked "
            "(mount_lock). On refusal, error_code is one of "
            "missing_field, invalid_drive_letter, mount_locked, "
            "file_not_found, mount_failed, or a mount-policy reason "
            "(does_not_resolve, not_regular_file, symlink_component, "
            "system_path, outside_whitelist, not_a_disk_image). This "
            "constructs real disk I/O with the emulator blocked under "
            "a 5-second deadline, and does not check that the target "
            "drive letter is currently mounted before replacing it."
        ),
        read_only=False,
        schema={
            "type": "object",
            "properties": {
                "drive": {
                    "type": "string",
                    "description": "Drive letter, e.g. 'A'.",
                },
                "image": {
                    "type": "string",
                    "description": "Path to the disk image - see mount_images for what's available.",
                },
            },
            "required": ["drive", "image"],
        },
        handler=lambda args: _drive_swap(client, args),
        feature=feature,
    )

    add_tool(
        name="mount_lock",
        description=(
            "Freeze the mount configuration: after this, every "
            "further mount attempt is refused - drive_swap, the "
            "guest's own MOUNT/IMGMOUNT/BOOT commands, all of it. "
            "Cannot be undone for the life of the process. Call this "
            "once an install's disk images are all mounted and you "
            "want to guarantee nothing changes the drive layout again."
        ),
        read_only=False,
        schema={"type": "object", "properties": {}},
        handler=lambda args: _mount_lock(client),
        feature=feature,
    )


def _drive_list(client):
    import mcp.types as types
    result = client.get("/api/v1/drive")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _mount_status(client):
    import mcp.types as types
    result = client.get("/api/v1/mount/policy")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _mount_images(client):
    import mcp.types as types
    result = client.get("/api/v1/mount/images")
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


def _drive_swap(client, args):
    import mcp.types as types
    body = {"drive": args["drive"], "image": args["image"]}
    result = client.post("/api/v1/drive/swap", json=body)
    return [types.TextContent(type="text", text=json.dumps(result))]


def _mount_lock(client):
    import mcp.types as types
    result = client.post("/api/v1/mount/lock")
    return [types.TextContent(type="text", text=json.dumps(result))]
