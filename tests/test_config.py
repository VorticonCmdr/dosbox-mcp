# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import os
import sys
from pathlib import Path

import pytest

from dosbox_mcp.config import (
    Config,
    ToolProtectedKey,
    default_token_path,
    update_config_file,
    validate_base_url,
    write_config_template,
)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8386",
    "http://localhost:8386",
    "https://[::1]:8386",
])
def test_loopback_urls_accepted(url):
    assert validate_base_url(url) == url


@pytest.mark.parametrize("url", [
    "http://10.0.0.5:8386",
    "http://example.com:8386",
    "ftp://127.0.0.1:8386",
    "127.0.0.1:8386",
])
def test_non_loopback_or_bad_scheme_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


class TestDefaultTokenPath:
    """The token path must match dosbox-automation's own per-OS config
    directory (src/misc/cross.cpp get_or_create_config_dir()), which is
    not what platformdirs would pick - notably macOS uses
    ~/Library/Preferences, not ~/Library/Application Support."""

    def test_macos_uses_preferences_not_application_support(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        path = default_token_path()
        assert path == (
            Path.home() / "Library" / "Preferences" / "dosbox-automation" /
            "webserver" / "api_token"
        )

    # Python 3.14 refuses to instantiate a WindowsPath (drive letter,
    # backslashes) on a non-Windows interpreter, so these two can only
    # run for real on Windows - default_token_path() uses the ambient
    # Path class, which is only WindowsPath there. Not simulatable
    # cross-platform without changing what production code returns.
    @pytest.mark.skipif(sys.platform != "win32", reason="needs a real WindowsPath")
    def test_windows_uses_localappdata(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
        path = default_token_path()
        assert path == (
            Path(r"C:\Users\test\AppData\Local") / "dosbox-automation" /
            "webserver" / "api_token"
        )

    @pytest.mark.skipif(sys.platform != "win32", reason="needs a real WindowsPath")
    def test_windows_xdg_override_wins(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("XDG_CONFIG_HOME", r"C:\custom")
        path = default_token_path()
        assert path == (
            Path(r"C:\custom") / "dosbox-automation" / "webserver" / "api_token"
        )

    def test_linux_uses_dot_config(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = default_token_path()
        assert path == (
            Path.home() / ".config" / "dosbox-automation" / "webserver" / "api_token"
        )

    def test_linux_xdg_config_home_override(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_token_path()
        assert path == tmp_path / "dosbox-automation" / "webserver" / "api_token"


class TestConfigLoad:
    def test_defaults_without_config_file(self, monkeypatch):
        monkeypatch.delenv("DOSBOX_TOKEN_FILE", raising=False)
        cfg = Config.load()
        assert cfg.port == 8386
        assert cfg.base_url == "http://127.0.0.1:8386"
        assert cfg.mode == "full"
        assert cfg.headless is False
        assert cfg.binary is None
        assert cfg.protocol is None
        assert cfg.token_file is None

    def test_toml_values_loaded(self, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text(
            'binary = "/opt/dosbox/dosbox"\n'
            "port = 9000\n"
            "headless = true\n"
            'protocol = "1.0"\n'
            'mode = "observe"\n'
            f'token_file = "{tmp_path / "tok"}"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        monkeypatch.delenv("DOSBOX_TOKEN_FILE", raising=False)
        cfg = Config.load()
        assert str(cfg.binary) == "/opt/dosbox/dosbox"
        assert cfg.port == 9000
        assert cfg.base_url == "http://127.0.0.1:9000"
        assert cfg.headless is True
        assert cfg.protocol == "1.0"
        assert cfg.mode == "observe"
        assert cfg.token_file == tmp_path / "tok"

    def test_env_url_overrides_toml_port(self, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text("port = 9000\n", encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        monkeypatch.setenv("DOSBOX_API_URL", "http://127.0.0.1:7777")
        cfg = Config.load()
        assert cfg.base_url == "http://127.0.0.1:7777"

    def test_env_token_file_overrides_toml(self, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text(f'token_file = "{tmp_path / "toml_tok"}"\n', encoding="utf-8")
        env_tok = tmp_path / "env_tok"
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        monkeypatch.setenv("DOSBOX_TOKEN_FILE", str(env_tok))
        cfg = Config.load()
        assert cfg.token_file == env_tok

    def test_unknown_toml_key_rejected(self, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text("headles = true\n", encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="headles"):
            Config.load()

    @pytest.mark.parametrize("line", [
        'mode = "root"',
        "port = 0",
        "port = 65536",
        'port = "8386"',
        'protocol = "banana"',
        'protocol = "1.0.0"',
        "headless = 1",
    ])
    def test_invalid_values_rejected(self, line, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text(line + "\n", encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError):
            Config.load()

    def test_malformed_toml_rejected(self, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text("port = = 1\n", encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError):
            Config.load()


class TestUpdateConfigFile:
    def test_creates_file_with_values(self, tmp_path):
        path = tmp_path / "config.toml"
        update_config_file(path, {"port": 9000, "headless": True})
        text = path.read_text(encoding="utf-8")
        assert "port = 9000" in text
        assert "headless = true" in text

    def test_preserves_human_comments(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            "# my carefully tuned setup\n"
            "port = 9000\n"
            '# the win31 binary, do not change\n'
            'binary = "/opt/dosbox/dosbox"\n',
            encoding="utf-8",
        )
        update_config_file(path, {"port": 9001})
        text = path.read_text(encoding="utf-8")
        assert "# my carefully tuned setup" in text
        assert "# the win31 binary, do not change" in text
        assert "port = 9001" in text
        assert 'binary = "/opt/dosbox/dosbox"' in text

    def test_validates_values(self, tmp_path):
        path = tmp_path / "config.toml"
        with pytest.raises(ValueError):
            update_config_file(path, {"port": 99999})
        assert not path.exists()

    def test_unknown_key_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="unknown"):
            update_config_file(tmp_path / "c.toml", {"prot": "1.0"})

    @pytest.mark.parametrize("key,value", [
        ("binary", "/some/other/binary"),
        ("mode", "full"),
    ])
    def test_protected_keys_rejected_when_flagged(self, key, value, tmp_path):
        # bridge_setup path: binary (code execution) and mode (privilege)
        # must be rejected loudly, not written.
        path = tmp_path / "config.toml"
        with pytest.raises(ToolProtectedKey, match=key):
            update_config_file(path, {key: value}, tool_facing=True)
        assert not path.exists()

    def test_protected_keys_allowed_for_cli(self, tmp_path):
        # The human-facing CLI may set anything.
        path = tmp_path / "config.toml"
        update_config_file(path, {"binary": "/opt/dosbox/dosbox",
                                  "mode": "observe"})
        text = path.read_text(encoding="utf-8")
        assert "/opt/dosbox/dosbox" in text
        assert "observe" in text


class TestConfigTemplate:
    def test_template_is_commented_and_loadable(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        write_config_template(path)
        text = path.read_text(encoding="utf-8")
        assert text.count("#") >= 6
        for key in ("binary", "port", "headless", "protocol", "mode",
                    "token_file"):
            assert key in text
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(path))
        monkeypatch.delenv("DOSBOX_TOKEN_FILE", raising=False)
        cfg = Config.load()
        assert cfg.port == 8386

    def test_template_refuses_to_overwrite(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("port = 9000\n", encoding="utf-8")
        with pytest.raises(FileExistsError):
            write_config_template(path)
