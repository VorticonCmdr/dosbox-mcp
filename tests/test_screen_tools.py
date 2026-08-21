# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

from dosbox_mcp.tools.screen import _screen_text


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
