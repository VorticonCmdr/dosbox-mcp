# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import jsonschema
import mcp.types as types
import pytest

from dosbox_mcp.tools.screen import _screen_capture, _screen_text


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def get(self, path, **kwargs):
        assert path == "/api/v1/video/text"
        return self._response


def _text(response):
    result = _screen_text(_FakeClient(response))
    assert len(result) == 1
    return result[0].text


def test_not_text_mode_reports_plainly_without_a_grid():
    text = _text({
        "is_text_mode": False,
        "bios_mode": 0x13,
        "cursor_row": 7,
        "cursor_col": 3,
    })
    assert "Not in a text mode" in text
    assert "0x13" in text
    assert "7,3" in text


def test_renders_a_real_grid_not_escaped_json():
    text = _text({
        "is_text_mode": True,
        "bios_mode": 3,
        "columns": 80,
        "rows": 25,
        "cursor_row": 12,
        "cursor_col": 40,
        "text_hash": "9a3f0000abcd1234",
        "text": "C:\\>dir\nHELLO.EXE",
    })
    # No literal backslash-n escape sequences - the old json.dumps()
    # shape put the whole grid on one line with those.
    assert "\\n" not in text
    lines = text.splitlines()
    assert lines[0] == (
        "80x25 mode 0x03 cursor 12,40 hash 0x9a3f0000abcd1234"
    )
    assert lines[1] == "0 C:\\>dir"
    assert lines[2] == "1 HELLO.EXE"


def test_drops_trailing_blank_rows_and_reports_the_count():
    text = _text({
        "is_text_mode": True,
        "bios_mode": 3,
        "columns": 80,
        "rows": 25,
        "cursor_row": 0,
        "cursor_col": 0,
        "text_hash": "0" * 16,
        "text": "\n".join(["ROW"] + [""] * 23),  # 1 real row, 23 blank
    })
    header, *body = text.splitlines()
    assert "(23 blank rows omitted)" in header
    assert body == ["0 ROW"]


def test_singular_blank_row_wording():
    text = _text({
        "is_text_mode": True, "bios_mode": 3, "columns": 80, "rows": 2,
        "cursor_row": 0, "cursor_col": 0, "text_hash": "0" * 16,
        "text": "ROW\n",
    })
    header = text.splitlines()[0]
    assert "(1 blank row omitted)" in header
    assert "rows omitted" not in header


def test_no_omitted_suffix_when_nothing_was_dropped():
    text = _text({
        "is_text_mode": True, "bios_mode": 3, "columns": 80, "rows": 1,
        "cursor_row": 0, "cursor_col": 0, "text_hash": "0" * 16,
        "text": "ROW",
    })
    assert "omitted" not in text.splitlines()[0]


def test_preserves_leading_whitespace_but_strips_trailing():
    # rstrip only: a leading indent inside a row is real screen content
    # (e.g. an indented menu item); trailing spaces are the padded
    # buffer, not content.
    text = _text({
        "is_text_mode": True, "bios_mode": 3, "columns": 80, "rows": 1,
        "cursor_row": 0, "cursor_col": 0, "text_hash": "0" * 16,
        "text": "   Indented item" + " " * 60,
    })
    body_line = text.splitlines()[1]
    assert body_line == "0    Indented item"


def test_bounds_to_the_engines_row_count_despite_the_trailing_newline():
    # dos_to_utf8's WithControlCodes mode puts a row-separating newline
    # after the last row too, so splitting on \n yields rows+1 elements
    # for a real engine response - the phantom last element must not be
    # counted as an extra blank row.
    text = _text({
        "is_text_mode": True, "bios_mode": 3, "columns": 80, "rows": 4,
        "cursor_row": 3, "cursor_col": 0, "text_hash": "0" * 16,
        "text": "A\nB\nC\nD\n",  # 4 real rows, trailing \n adds a 5th split element
    })
    header, *body = text.splitlines()
    assert "omitted" not in header
    assert body == ["0 A", "1 B", "2 C", "3 D"]


def test_row_numbers_are_zero_based_matching_cursor_row():
    # cursor_row from the API is 0-based (BIOS-native); the printed row
    # numbers must use the same base so an agent can directly correlate
    # "cursor 3,x" with the "3 <row text>" line.
    text = _text({
        "is_text_mode": True, "bios_mode": 3, "columns": 80, "rows": 4,
        "cursor_row": 3, "cursor_col": 0, "text_hash": "0" * 16,
        "text": "A\nB\nC\nD",
    })
    lines = text.splitlines()
    assert lines[1] == "0 A"
    assert lines[4] == "3 D"


# ---------------------------------------------------------------------------
# screen_capture
# ---------------------------------------------------------------------------


class _FakeCaptureClient:
    def __init__(self, response=b"\x89PNG..."):
        self._response = response
        self.last_path = None
        self.last_params = None

    def get(self, path, params=None, **kwargs):
        self.last_path = path
        self.last_params = params
        return self._response


def _capture_schema():
    from dosbox_mcp.tools import screen

    captured = {}

    def add_tool(name, schema=None, **kwargs):
        if name == "screen_capture":
            captured["schema"] = schema

    screen.register(server=None, client=None, add_tool=add_tool)
    return captured["schema"]


def test_default_capture_only_asks_for_png():
    client = _FakeCaptureClient()
    _screen_capture(client, {})

    assert client.last_path == "/api/v1/video/frame"
    assert client.last_params == {"format": "png"}


def test_mode_is_forwarded():
    client = _FakeCaptureClient()
    _screen_capture(client, {"mode": "rendered"})

    assert client.last_params["mode"] == "rendered"


def test_format_raw_is_rejected_even_bypassing_schema_validation():
    # The schema's format enum (png/jpeg only) already blocks this
    # through the real MCP dispatch path - this calls the handler
    # directly, the same way a caller who bypassed schema validation
    # would, to confirm the handler's own defense-in-depth check holds:
    # 'raw' pixel bytes must never come back claiming an image/* mimeType.
    client = _FakeCaptureClient()
    result = _screen_capture(client, {"format": "raw"})

    assert client.last_path is None  # never reached the engine
    assert isinstance(result, types.CallToolResult)
    assert result.isError


def test_quality_is_forwarded_only_for_jpeg():
    client = _FakeCaptureClient()
    _screen_capture(client, {"format": "jpeg", "quality": 50})
    assert client.last_params == {"format": "jpeg", "quality": 50}

    _screen_capture(client, {"format": "png", "quality": 50})
    assert "quality" not in client.last_params


def test_png_level_is_forwarded_only_for_png():
    client = _FakeCaptureClient()
    _screen_capture(client, {"format": "png", "png_level": 9})
    assert client.last_params == {"format": "png", "png_level": 9}

    _screen_capture(client, {"format": "jpeg", "png_level": 9})
    assert "png_level" not in client.last_params


def test_scale_is_forwarded():
    client = _FakeCaptureClient()
    _screen_capture(client, {"scale": 4})
    assert client.last_params["scale"] == 4


def test_crop_fields_are_forwarded_together():
    client = _FakeCaptureClient()
    _screen_capture(
        client, {"crop_x": 1, "crop_y": 2, "crop_w": 3, "crop_h": 4}
    )
    assert client.last_params == {
        "format": "png", "crop_x": 1, "crop_y": 2, "crop_w": 3, "crop_h": 4,
    }


def test_result_mime_type_matches_the_requested_format():
    client = _FakeCaptureClient()

    png_result = _screen_capture(client, {})
    assert png_result[0].mimeType == "image/png"

    jpeg_result = _screen_capture(client, {"format": "jpeg"})
    assert jpeg_result[0].mimeType == "image/jpeg"


def test_result_data_is_base64_of_the_raw_response():
    import base64

    client = _FakeCaptureClient(response=b"raw-bytes")
    result = _screen_capture(client, {})

    assert base64.b64decode(result[0].data) == b"raw-bytes"


# Schema checks run against the real registered schema (not a hand-copied
# duplicate), so a future edit to screen.py can't silently drift from what
# these tests assert.


def test_schema_rejects_an_unknown_property():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"bogus": 1}, _capture_schema())


def test_schema_rejects_a_partial_crop_rectangle():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"crop_x": 0, "crop_y": 0}, _capture_schema())


def test_schema_rejects_exactly_one_crop_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"crop_x": 0}, _capture_schema())


def test_schema_rejects_exactly_three_crop_fields():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"crop_x": 0, "crop_y": 0, "crop_w": 10}, _capture_schema()
        )


def test_schema_rejects_a_crop_coordinate_past_the_engines_bound():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"crop_x": 65536, "crop_y": 0, "crop_w": 1, "crop_h": 1},
            _capture_schema(),
        )


def test_schema_accepts_a_full_crop_rectangle():
    jsonschema.validate(
        {"crop_x": 0, "crop_y": 0, "crop_w": 10, "crop_h": 10},
        _capture_schema(),
    )


def test_schema_accepts_no_crop_at_all():
    jsonschema.validate({}, _capture_schema())


def test_schema_rejects_a_scale_not_in_the_fixed_divisor_set():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"scale": 3}, _capture_schema())


def test_schema_rejects_an_out_of_range_quality():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"quality": 0}, _capture_schema())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"quality": 101}, _capture_schema())


def test_schema_rejects_an_out_of_range_png_level():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"png_level": -1}, _capture_schema())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"png_level": 10}, _capture_schema())
