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
    default_ghidra_map_path,
    default_token_path,
    engine_config_dir,
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


class TestEngineConfigDir:
    """engine_config_dir(home) must match get_or_create_config_dir()
    (src/misc/cross.cpp) for a spawned instance whose HOME and
    XDG_CONFIG_HOME are both pointed at `home` - the isolation
    InstanceManager.start() always applies."""

    def test_macos_uses_preferences_ignoring_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert engine_config_dir(tmp_path) == (
            tmp_path / "Library" / "Preferences" / "dosbox-automation"
        )

    def test_linux_or_windows_uses_home_directly(self, monkeypatch, tmp_path):
        # Not home/.config - XDG_CONFIG_HOME is set to home itself by
        # InstanceManager, not home/.config (that's the integration test
        # harness's own choice, a different caller).
        monkeypatch.setattr(sys, "platform", "linux")
        assert engine_config_dir(tmp_path) == tmp_path / "dosbox-automation"


class TestDefaultGhidraMapPath:
    def test_defaults_under_the_bridges_own_config_dir(self, monkeypatch):
        monkeypatch.delenv("DOSBOX_MCP_GHIDRA_MAP", raising=False)
        from platformdirs import user_config_dir
        path = default_ghidra_map_path()
        assert path == Path(user_config_dir("dosbox-mcp")) / "ghidra_map.json"

    def test_env_override_wins(self, monkeypatch, tmp_path):
        override = tmp_path / "custom_map.json"
        monkeypatch.setenv("DOSBOX_MCP_GHIDRA_MAP", str(override))
        assert default_ghidra_map_path() == override


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
        assert cfg.mount_allowed_bases == []
        assert cfg.mount_allowed_image_roots == []

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


class TestMountPolicyKeys:
    """mount_allowed_bases/mount_allowed_image_roots: written into a
    spawned instance's own primary config by InstanceManager, so
    validated as strictly here as the engine's own ParsePathList - see
    _validate_path_list's docstring for why (a bad entry there is
    silently dropped with only a log warning)."""

    def test_toml_arrays_of_existing_dirs_loaded(self, tmp_path, monkeypatch):
        games = tmp_path / "games"
        games.mkdir()
        images = tmp_path / "images"
        images.mkdir()
        toml = tmp_path / "config.toml"
        toml.write_text(
            f'mount_allowed_bases = ["{games}"]\n'
            f'mount_allowed_image_roots = ["{images}"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        cfg = Config.load()
        assert cfg.mount_allowed_bases == [games]
        assert cfg.mount_allowed_image_roots == [images]

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_relative_path_rejected(self, key, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text(f'{key} = ["relative/dir"]\n', encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="absolute"):
            Config.load()

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_nonexistent_directory_rejected(self, key, tmp_path, monkeypatch):
        missing = tmp_path / "does-not-exist"
        toml = tmp_path / "config.toml"
        toml.write_text(f'{key} = ["{missing}"]\n', encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="does not exist"):
            Config.load()

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_file_not_a_directory_rejected(self, key, tmp_path, monkeypatch):
        a_file = tmp_path / "not-a-dir"
        a_file.write_text("x", encoding="utf-8")
        toml = tmp_path / "config.toml"
        toml.write_text(f'{key} = ["{a_file}"]\n', encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="does not exist"):
            Config.load()

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_more_than_five_entries_rejected(self, key, tmp_path, monkeypatch):
        dirs = []
        for i in range(6):
            d = tmp_path / f"dir{i}"
            d.mkdir()
            dirs.append(str(d))
        toml = tmp_path / "config.toml"
        array = ", ".join(f'"{d}"' for d in dirs)
        toml.write_text(f"{key} = [{array}]\n", encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="at most 5"):
            Config.load()

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_symlinked_path_rejected(self, key, tmp_path, monkeypatch):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        toml = tmp_path / "config.toml"
        toml.write_text(f'{key} = ["{link}"]\n', encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="symlink"):
            Config.load()

    @pytest.mark.parametrize("key", ["mount_allowed_bases", "mount_allowed_image_roots"])
    def test_non_list_value_rejected(self, key, tmp_path, monkeypatch):
        toml = tmp_path / "config.toml"
        toml.write_text(f'{key} = "{tmp_path}"\n', encoding="utf-8")
        monkeypatch.setenv("DOSBOX_MCP_CONFIG", str(toml))
        with pytest.raises(ValueError, match="list of path strings"):
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
        ("mount_allowed_bases", ["/some/dir"]),
        ("mount_allowed_image_roots", ["/some/dir"]),
    ])
    def test_protected_keys_rejected_when_flagged(self, key, value, tmp_path):
        # bridge_setup path: binary (code execution), mode (privilege),
        # and the mount whitelists (filesystem access) must be rejected
        # loudly, not written. The protected-key check runs before
        # per-key validation, so an unresolvable dummy value here still
        # proves the rejection, not a validation failure.
        path = tmp_path / "config.toml"
        with pytest.raises(ToolProtectedKey, match=key):
            update_config_file(path, {key: value}, tool_facing=True)
        assert not path.exists()

    def test_protected_keys_allowed_for_cli(self, tmp_path):
        # The human-facing CLI may set anything.
        allowed = tmp_path / "games"
        allowed.mkdir()
        path = tmp_path / "config.toml"
        update_config_file(path, {"binary": "/opt/dosbox/dosbox",
                                  "mode": "observe",
                                  "mount_allowed_bases": [str(allowed)]})
        text = path.read_text(encoding="utf-8")
        assert "/opt/dosbox/dosbox" in text
        assert "observe" in text
        assert str(allowed) in text


class TestConfigTemplate:
    def test_template_is_commented_and_loadable(self, tmp_path, monkeypatch):
        path = tmp_path / "config.toml"
        write_config_template(path)
        text = path.read_text(encoding="utf-8")
        assert text.count("#") >= 6
        for key in ("binary", "port", "headless", "protocol", "mode",
                    "token_file", "mount_allowed_bases",
                    "mount_allowed_image_roots"):
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
