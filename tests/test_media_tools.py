# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import json

from dosbox_mcp.tools.media import (
    _capture_start,
    _drive_list,
    _drive_swap,
    _mount_images,
    _mount_lock,
    _mount_status,
)


class _FakeClient:
    def __init__(self, response=None):
        self._response = response
        self.last_method = None
        self.last_path = None
        self.last_kwargs = None

    def get(self, path, **kwargs):
        self.last_method = "get"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response

    def post(self, path, **kwargs):
        self.last_method = "post"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


def test_drive_list_gets_the_drive_route():
    response = {"drives": [{"letter": "A", "mounted": False}]}
    client = _FakeClient(response)

    result = _drive_list(client)

    assert client.last_method == "get"
    assert client.last_path == "/api/v1/drive"
    assert json.loads(result[0].text) == response


def test_mount_status_gets_the_policy_route_not_the_bare_lock_route():
    response = {"locked": False, "allowed_bases": [],
                "allowed_image_roots": ["/games/images"]}
    client = _FakeClient(response)

    result = _mount_status(client)

    assert client.last_path == "/api/v1/mount/policy"
    assert json.loads(result[0].text) == response


def test_mount_images_gets_the_images_route():
    response = {"roots": [{"root": "/games/images", "images": [],
                          "truncated": False}]}
    client = _FakeClient(response)

    result = _mount_images(client)

    assert client.last_path == "/api/v1/mount/images"
    assert json.loads(result[0].text) == response


def test_drive_swap_posts_drive_and_image_as_the_json_body():
    client = _FakeClient({"status": "ok", "drive": "A"})

    _drive_swap(client, {"drive": "A", "image": "/games/images/disk2.img"})

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/drive/swap"
    assert client.last_kwargs["json"] == {
        "drive": "A", "image": "/games/images/disk2.img"}


def test_mount_lock_posts_with_no_body():
    client = _FakeClient({"status": "locked"})

    result = _mount_lock(client)

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/mount/lock"
    assert "json" not in client.last_kwargs
    assert json.loads(result[0].text) == {"status": "locked"}


def test_capture_start_with_no_args_posts_with_no_body():
    client = _FakeClient({"status": "recording", "mode": "raw"})

    result = _capture_start(client, {})

    assert client.last_method == "post"
    assert client.last_path == "/api/v1/capture/video/start"
    assert "json" not in client.last_kwargs
    assert json.loads(result[0].text) == {"status": "recording", "mode": "raw"}


def test_capture_start_forwards_mode_and_compression():
    client = _FakeClient({"status": "recording", "mode": "rendered",
                          "compression": 3})

    _capture_start(client, {"mode": "rendered", "compression": 3})

    assert client.last_kwargs["json"] == {"mode": "rendered", "compression": 3}


def test_capture_start_forwards_only_the_args_given():
    client = _FakeClient({"status": "recording", "mode": "raw"})

    _capture_start(client, {"compression": 5})

    assert client.last_kwargs["json"] == {"compression": 5}
