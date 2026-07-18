# INSTALL - getting the bridge onto your machine

Like every good DOS game, this one comes with an install sheet. It is
short, because installation is short.

## Requirements

- Python 3.11 or newer.
- A dosbox build that speaks the automation API. The reference is
  dosbox-automation, downloads at https://dosbox-automation.org.
- An MCP client (Claude Code, Claude Desktop, or anything else that
  speaks the Model Context Protocol over stdio).

## Install

```
pip install dosbox-mcp
```

or, without installing anything permanently:

```
uvx dosbox-mcp
```

## Register with your MCP client

Claude Code, one line:

```
claude mcp add dosbox -- dosbox-mcp
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dosbox": {
      "command": "dosbox-mcp"
    }
  }
}
```

## First contact

Start a dosbox instance with the webserver enabled and a token file:

```
[webserver]
webserver_enabled = true
webserver_token_file = true
```

in its config, then ask your agent to call `bridge_status`. If it says
connected, you are done. If not, the message tells you exactly what is
missing - and TECH-SUPPORT.md in this folder covers the rest.

Optional: write yourself a config to tune ports, spawning, and what an
agent may do:

```
dosbox-mcp setup --init
```

That drops a fully commented file at your user config dir
(`~/.config/dosbox-mcp/config.toml` on Linux). The comments are the
manual for it. MANUAL.md in this folder explains everything else.
