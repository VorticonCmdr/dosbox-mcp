# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json


def register(server, client, add_tool, feature=None):
    add_tool(
        name="dosbox_status",
        description=(
            "Machine state: what program is running, which drives are "
            "mounted, and the engine version - one call answers 'what is "
            "the machine doing right now'. This is a curated summary, not "
            "a passthrough of the underlying routes; set detail:true for "
            "the full raw payload (every unmounted drive letter, build "
            "capabilities and limits, internal timing fields)."
        ),
        risk="read",
        title="Machine Status",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "detail": {
                    "type": "boolean",
                    "description": (
                        "Include the full raw status/drives/info payloads "
                        "alongside the summary (default false)."
                    ),
                },
            },
        },
        handler=lambda args: _status(client, args),
    )

    add_tool(
        name="dosbox_shutdown",
        description="Shut down the emulator. Irreversible.",
        risk="destructive",
        title="Shut Down Emulator",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _shutdown(client),
    )

    add_tool(
        name="session_info",
        description=(
            "Connection details for driving the REST API directly: base "
            "URL, token location, and a curl example that reads the token "
            "from its file. Use this when you need an endpoint the MCP "
            "tools don't wrap, such as saving a video frame to a file. "
            "The token value itself is never returned - it stays out of "
            "transcripts by design."
        ),
        risk="read",
        title="Session Info",
        schema={"type": "object", "properties": {}},
        handler=lambda args: _session_info(client),
    )


def _status(client, args):
    import mcp.types as types

    # /api/v1/program/state is a strict subset of /api/v1/status
    # (segment_name/canonical_name/is_shell/is_booted, just under a
    # different key for the program name) - fetching it too would only
    # repeat data /status already has, so it's not called here at all.
    status = client.get("/api/v1/status")
    drives = client.get("/api/v1/drive").get("drives", [])
    info = client.get("/api/v1/dosbox/info")

    mounted_drives = {
        d["letter"]: {
            "type": d.get("type"),
            "info": d.get("info"),
            "read_only": d.get("read_only"),
            "removable": d.get("removable"),
        }
        for d in drives
        if d.get("mounted")
    }

    out = {
        "running": status.get("running"),
        "emulation": status.get("emulation"),
        "program": status.get("program"),
        "canonical_name": status.get("canonical_name"),
        "is_shell": status.get("is_shell"),
        "is_booted": status.get("is_booted"),
        "frame": status.get("frame"),
        "version": info.get("version"),
        "mounted_drives": mounted_drives,
    }
    if args.get("detail"):
        out["status"] = status
        out["drives"] = drives
        out["info"] = info

    return [types.TextContent(type="text", text=json.dumps(out))]


def _shutdown(client):
    import mcp.types as types
    result = client.post("/api/v1/dosbox/shutdown")
    return [types.TextContent(type="text", text=json.dumps(result))]


def _session_info(client):
    import mcp.types as types
    from dosbox_mcp.config import default_token_path, read_token

    base_url = client.base_url
    token = read_token()
    token_path = default_token_path()

    # The token value never enters the transcript (design rule,
    # self-audit 2026-07-17). The curl example reads it from the file
    # at run time instead.
    info = {"base_url": base_url, "token_file": str(token_path)}
    if token:
        info["token"] = "present"  # nosec B105 - status word, not a secret
        info["example"] = (
            f'curl -H "Authorization: Bearer $(cat {token_path})" '
            f"{base_url}/api/v1/status"
        )
    else:
        info["token"] = "absent"  # nosec B105 - status word, not a secret
        info["note"] = (
            "No token available yet: DOSBOX_API_TOKEN is unset and the "
            "token file does not exist. Start dosbox with "
            "webserver_token_file=true, or export the env var."
        )
    return [types.TextContent(type="text", text=json.dumps(info, indent=2))]
