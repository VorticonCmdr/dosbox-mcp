# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import base64
import json

import pytest

from dosbox_mcp.client import DosboxError
from dosbox_mcp.tools.memory import (
    MAX_LENGTH_BYTES,
    MAX_RENDERED_VIEW_BYTES,
    _mem_alloc,
    _mem_allocations,
    _mem_diff,
    _mem_free,
    _mem_path,
    _mem_read,
    _mem_scan,
    _mem_search,
    _mem_snapshot,
    _mem_write,
    _render_hex,
    _resolve_offset,
    _resolve_segment,
)


class _FakeClient:
    def __init__(self, response=None, put_error=None):
        self._response = response
        self._put_error = put_error
        self.last_method = None
        self.last_path = None
        self.last_kwargs = None

    def get(self, path, **kwargs):
        self.last_method = "get"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response

    def put(self, path, **kwargs):
        self.last_method = "put"
        self.last_path = path
        self.last_kwargs = kwargs
        if self._put_error is not None:
            raise self._put_error
        return self._response

    def post(self, path, **kwargs):
        self.last_method = "post"
        self.last_path = path
        self.last_kwargs = kwargs
        return self._response


def _mem_response(addr, raw_bytes, registers=None):
    return {
        "memory": {"addr": addr, "data": base64.b64encode(raw_bytes).decode()},
        "registers": registers if registers is not None else {"eax": 1},
    }


# ---------------------------------------------------------------------------
# _resolve_segment / _mem_path
# ---------------------------------------------------------------------------


def test_resolve_segment_accepts_register_names_case_insensitively():
    assert _resolve_segment("ds") == "ds"
    assert _resolve_segment("DS") == "ds"
    assert _resolve_segment("Es") == "es"


def test_resolve_segment_accepts_numeric_int():
    assert _resolve_segment(0x1234) == "4660"


def test_resolve_segment_accepts_numeric_string():
    assert _resolve_segment("0x1234") == "4660"
    assert _resolve_segment("4660") == "4660"


def test_resolve_segment_rejects_unknown_string():
    with pytest.raises(ValueError, match="register name"):
        _resolve_segment("banana")


def test_resolve_segment_rejects_out_of_range_int():
    with pytest.raises(ValueError, match="0x0000..0xFFFF"):
        _resolve_segment(0x10000)
    with pytest.raises(ValueError, match="0x0000..0xFFFF"):
        _resolve_segment(-1)


def test_resolve_segment_rejects_bool():
    # bool is a subclass of int in Python - True/False must not silently
    # resolve to segment 1/0.
    with pytest.raises(ValueError, match="string or integer"):
        _resolve_segment(True)


def test_resolve_offset_accepts_full_uint32_range():
    assert _resolve_offset(0) == 0
    assert _resolve_offset(0xFFFFFFFF) == 0xFFFFFFFF


def test_resolve_offset_rejects_negative():
    with pytest.raises(ValueError, match="0x00000000..0xFFFFFFFF"):
        _resolve_offset(-1)


def test_resolve_offset_rejects_over_uint32_max():
    with pytest.raises(ValueError, match="0x00000000..0xFFFFFFFF"):
        _resolve_offset(0x100000000)


def test_resolve_offset_rejects_float():
    with pytest.raises(ValueError, match="must be an integer"):
        _resolve_offset(3.0)


def test_resolve_offset_rejects_bool():
    with pytest.raises(ValueError, match="must be an integer"):
        _resolve_offset(True)


def test_mem_path_omits_segment_when_none():
    assert _mem_path(0x1234, None, 100) == "/api/v1/memory/4660/100"
    assert _mem_path(0x1234, None) == "/api/v1/memory/4660"


def test_mem_path_includes_register_segment():
    assert _mem_path(0x50, "ds", 16) == "/api/v1/memory/ds/80/16"


def test_mem_path_includes_numeric_segment():
    assert _mem_path(0x50, 0x1000, 16) == "/api/v1/memory/4096/80/16"


# ---------------------------------------------------------------------------
# _render_hex
# ---------------------------------------------------------------------------


def test_render_hex_full_line():
    text = _render_hex(bytes(range(16)))
    assert text.startswith("0000  00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f")
    # Unprintable control bytes render as dots in the ASCII column.
    assert text.endswith("................")


def test_render_hex_printable_ascii_column():
    text = _render_hex(b"Hello, DOS!")
    assert "Hello, DOS!" in text


def test_render_hex_partial_final_line_padded():
    lines = _render_hex(bytes(range(20))).splitlines()
    assert len(lines) == 2
    assert lines[1].startswith("0010  ")


# ---------------------------------------------------------------------------
# mem_read
# ---------------------------------------------------------------------------


def test_mem_read_defaults_to_base64_view_and_no_registers():
    client = _FakeClient(_mem_response(100, b"\x01\x02\x03"))
    result = _mem_read(client, {"offset": 100})
    body = json.loads(result[0].text)
    assert body["addr"] == 100
    assert base64.b64decode(body["data"]) == b"\x01\x02\x03"
    assert "registers" not in body
    # Default length applied - not passed to the FakeClient as an arg,
    # but reflected in the path.
    assert client.last_path == "/api/v1/memory/100/256"


def test_mem_read_include_registers():
    client = _FakeClient(_mem_response(100, b"\x01", registers={"eax": 42}))
    result = _mem_read(client, {"offset": 100, "include_registers": True})
    body = json.loads(result[0].text)
    assert body["registers"] == {"eax": 42}


def test_mem_read_hex_view():
    client = _FakeClient(_mem_response(0, b"AB"))
    result = _mem_read(client, {"offset": 0, "length": 2, "view": "hex"})
    body = json.loads(result[0].text)
    assert "hex" in body
    assert "41 42" in body["hex"]


def test_mem_read_bytes_view():
    client = _FakeClient(_mem_response(0, b"\x01\x02\x03"))
    result = _mem_read(client, {"offset": 0, "length": 3, "view": "bytes"})
    body = json.loads(result[0].text)
    assert body["bytes"] == [1, 2, 3]


def test_mem_read_words_view_little_endian():
    client = _FakeClient(_mem_response(0, b"\x34\x12\x78\x56"))
    result = _mem_read(client, {"offset": 0, "length": 4, "view": "words"})
    body = json.loads(result[0].text)
    assert body["words"] == [0x1234, 0x5678]


def test_mem_read_dwords_view_little_endian():
    client = _FakeClient(_mem_response(0, b"\x78\x56\x34\x12"))
    result = _mem_read(client, {"offset": 0, "length": 4, "view": "dwords"})
    body = json.loads(result[0].text)
    assert body["dwords"] == [0x12345678]


def test_mem_read_text_view_uses_cp437():
    # 0xB0-0xB2: light/medium/dark shade blocks in CP437, not valid UTF-8
    # or Latin-1 in any useful sense - a plain decode('utf-8') would
    # raise or mangle these.
    client = _FakeClient(_mem_response(0, b"\xb0\xb1\xb2"))
    result = _mem_read(client, {"offset": 0, "length": 3, "view": "text"})
    body = json.loads(result[0].text)
    assert body["text"] == "░▒▓"


def test_mem_read_uses_segment_in_path():
    client = _FakeClient(_mem_response(0x2000, b"\x00"))
    _mem_read(client, {"offset": 0x50, "segment": "ds", "length": 1})
    assert client.last_path == "/api/v1/memory/ds/80/1"


def test_mem_read_rejects_bad_segment_without_calling_client():
    client = _FakeClient()
    result = _mem_read(client, {"offset": 0, "segment": "nope"})
    assert result.isError is True
    assert client.last_method is None


def test_mem_read_rejects_length_over_cap():
    client = _FakeClient()
    result = _mem_read(client, {"offset": 0, "length": MAX_LENGTH_BYTES + 1})
    assert result.isError is True
    assert client.last_method is None


def test_mem_read_rejects_rendered_view_over_its_tighter_cap():
    client = _FakeClient()
    result = _mem_read(client, {
        "offset": 0, "length": MAX_RENDERED_VIEW_BYTES + 1, "view": "hex",
    })
    assert result.isError is True
    assert client.last_method is None


def test_mem_read_rejects_odd_length_for_words():
    client = _FakeClient()
    result = _mem_read(client, {"offset": 0, "length": 3, "view": "words"})
    assert result.isError is True


def test_mem_read_rejects_unknown_view():
    client = _FakeClient()
    result = _mem_read(client, {"offset": 0, "view": "banana"})
    assert result.isError is True


def test_mem_read_rejects_negative_or_oversized_offset_without_calling_client():
    client = _FakeClient()
    result = _mem_read(client, {"offset": -1})
    assert result.isError is True
    assert client.last_method is None


def test_mem_read_rejects_size_mismatched_response_instead_of_crashing():
    # The engine always returns exactly the requested length; a
    # response that doesn't match (a non-conforming or compromised
    # connection target) must be rejected explicitly, not fed to
    # _render_view - whose words/dwords branches index past a
    # shorter-than-expected buffer.
    client = _FakeClient(_mem_response(0, b"\x01\x02\x03"))  # 3 bytes, not 4
    result = _mem_read(client, {"offset": 0, "length": 4, "view": "words"})
    assert result.isError is True


# ---------------------------------------------------------------------------
# mem_write
# ---------------------------------------------------------------------------


def test_mem_write_without_expected_sends_no_if_match_header():
    client = _FakeClient({"memory": {"addr": 100}})
    _mem_write(client, {"offset": 100, "data": "AQ=="})
    assert "headers" not in client.last_kwargs
    assert client.last_kwargs["json"] == {"data": "AQ=="}


def test_mem_write_with_expected_sends_if_match_header():
    client = _FakeClient({"memory": {"addr": 100}})
    _mem_write(client, {"offset": 100, "data": "AQ==", "expected": "AA=="})
    assert client.last_kwargs["headers"] == {"If-Match": "AA=="}


def test_mem_write_uses_segment_in_path():
    client = _FakeClient({"memory": {"addr": 0x2050}})
    _mem_write(client, {"offset": 0x50, "segment": "es", "data": "AQ=="})
    assert client.last_path == "/api/v1/memory/es/80"


def test_mem_write_success_returns_flattened_status_and_addr():
    client = _FakeClient({"memory": {"addr": 100}})
    result = _mem_write(client, {"offset": 100, "data": "AQ=="})
    assert json.loads(result[0].text) == {"status": "ok", "addr": 100}


def test_mem_write_conflict_returns_typed_data_not_an_error():
    conflict_body = {"memory": {"addr": 100, "data": base64.b64encode(b"\xff").decode()}}
    error = DosboxError(412, "precondition_failed", str(conflict_body), body=conflict_body)
    client = _FakeClient(put_error=error)

    result = _mem_write(client, {
        "offset": 100, "data": "AQ==", "expected": "AA==",
    })

    # A normal tool-result list, not a CallToolResult(isError=True) - a
    # conflict is an expected, actionable outcome, not an error.
    assert isinstance(result, list)
    body = json.loads(result[0].text)
    assert body["conflict"] is True
    assert body["addr"] == 100
    assert base64.b64decode(body["actual_data"]) == b"\xff"


def test_mem_write_conflict_with_malformed_body_fails_loudly():
    # e.body missing 'memory' entirely (e.g. a proxy error page, a
    # truncated body) must not silently produce {addr: null, ...} as if
    # it were a legitimate conflict.
    error = DosboxError(412, "precondition_failed", "boom", body={})
    client = _FakeClient(put_error=error)
    result = _mem_write(client, {"offset": 100, "data": "AQ==", "expected": "AA=="})
    assert result.isError is True


def test_mem_write_conflict_with_non_dict_memory_fails_loudly_not_attributeerror():
    error = DosboxError(412, "precondition_failed", "boom", body={"memory": None})
    client = _FakeClient(put_error=error)
    result = _mem_write(client, {"offset": 100, "data": "AQ==", "expected": "AA=="})
    assert result.isError is True


def test_mem_write_conflict_with_memory_missing_data_fails_loudly():
    error = DosboxError(412, "precondition_failed", "boom", body={"memory": {"addr": 100}})
    client = _FakeClient(put_error=error)
    result = _mem_write(client, {"offset": 100, "data": "AQ==", "expected": "AA=="})
    assert result.isError is True


def test_mem_write_non_412_error_propagates():
    error = DosboxError(500, "internal_error", "boom")
    client = _FakeClient(put_error=error)
    with pytest.raises(DosboxError):
        _mem_write(client, {"offset": 100, "data": "AQ=="})


def test_mem_write_rejects_bad_segment_without_calling_client():
    client = _FakeClient()
    result = _mem_write(client, {"offset": 0, "segment": "nope", "data": "AQ=="})
    assert result.isError is True
    assert client.last_method is None


def test_mem_write_rejects_negative_or_oversized_offset_without_calling_client():
    client = _FakeClient()
    result = _mem_write(client, {"offset": -1, "data": "AQ=="})
    assert result.isError is True
    assert client.last_method is None


def test_mem_write_rejects_malformed_base64_without_calling_client():
    client = _FakeClient()
    result = _mem_write(client, {"offset": 0, "data": "not valid base64!!"})
    assert result.isError is True
    assert client.last_method is None


def test_mem_write_rejects_data_over_the_length_cap_without_calling_client():
    oversized = base64.b64encode(b"\x00" * (MAX_LENGTH_BYTES + 1)).decode()
    client = _FakeClient()
    result = _mem_write(client, {"offset": 0, "data": oversized})
    assert result.isError is True
    assert client.last_method is None


def test_mem_write_accepts_data_at_exactly_the_length_cap():
    at_cap = base64.b64encode(b"\x00" * MAX_LENGTH_BYTES).decode()
    client = _FakeClient({"memory": {"addr": 0}})
    result = _mem_write(client, {"offset": 0, "data": at_cap})
    assert isinstance(result, list)
    assert client.last_method == "put"


# ---------------------------------------------------------------------------
# mem_search
# ---------------------------------------------------------------------------


def test_mem_search_omits_limit_when_not_given():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False})
    _mem_search(client, {"start": 0, "end": 16, "value": 1})
    assert "limit" not in client.last_kwargs["json"]


def test_mem_search_passes_limit_through():
    client = _FakeClient({"matches": [0], "total": 1, "truncated": False})
    _mem_search(client, {"start": 0, "end": 16, "value": 1, "limit": 10})
    assert client.last_kwargs["json"]["limit"] == 10


def test_mem_search_returns_total_and_truncated():
    response = {"matches": [0, 4], "total": 500, "truncated": True}
    client = _FakeClient(response)
    result = _mem_search(client, {"start": 0, "end": 1000, "value": 1})
    body = json.loads(result[0].text)
    assert body["total"] == 500
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# mem_scan
# ---------------------------------------------------------------------------


def test_mem_scan_posts_pattern_and_range():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False})
    _mem_scan(client, {"pattern": "8B 46 ?? 50 E8", "start": 0, "end": 1000})
    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/scan"
    assert client.last_kwargs["json"] == {
        "pattern": "8B 46 ?? 50 E8",
        "start": 0,
        "end": 1000,
    }


def test_mem_scan_omits_limit_when_not_given():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False})
    _mem_scan(client, {"pattern": "8B", "start": 0, "end": 16})
    assert "limit" not in client.last_kwargs["json"]


def test_mem_scan_passes_limit_through():
    client = _FakeClient({"matches": [0], "total": 1, "truncated": False})
    _mem_scan(client, {"pattern": "8B", "start": 0, "end": 16, "limit": 10})
    assert client.last_kwargs["json"]["limit"] == 10


def test_mem_scan_returns_total_and_truncated():
    response = {"matches": [0, 4], "total": 500, "truncated": True}
    client = _FakeClient(response)
    result = _mem_scan(client, {"pattern": "90", "start": 0, "end": 1000})
    body = json.loads(result[0].text)
    assert body["total"] == 500
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# mem_snapshot / mem_diff
# ---------------------------------------------------------------------------


def test_mem_snapshot_posts_start_and_end():
    client = _FakeClient({"handle": 1, "start": 0, "end": 16, "bytes": 16})
    _mem_snapshot(client, {"start": 0, "end": 16})
    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/snapshot"
    assert client.last_kwargs["json"] == {"start": 0, "end": 16}


def test_mem_snapshot_returns_handle():
    client = _FakeClient({"handle": 42, "start": 0, "end": 16, "bytes": 16})
    result = _mem_snapshot(client, {"start": 0, "end": 16})
    body = json.loads(result[0].text)
    assert body["handle"] == 42


def test_mem_diff_posts_handle_and_op():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False,
                          "candidates": 0})
    _mem_diff(client, {"handle": 1, "op": "changed"})
    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/diff"
    assert client.last_kwargs["json"] == {"handle": 1, "op": "changed"}


def test_mem_diff_omits_width_and_limit_when_not_given():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False,
                          "candidates": 0})
    _mem_diff(client, {"handle": 1, "op": "unchanged"})
    assert "width" not in client.last_kwargs["json"]
    assert "limit" not in client.last_kwargs["json"]


def test_mem_diff_passes_width_and_limit_through():
    client = _FakeClient({"matches": [], "total": 0, "truncated": False,
                          "candidates": 0})
    _mem_diff(client, {"handle": 1, "op": "increased", "width": 2, "limit": 10})
    assert client.last_kwargs["json"]["width"] == 2
    assert client.last_kwargs["json"]["limit"] == 10


def test_mem_diff_returns_candidates_and_total():
    response = {
        "matches": [{"addr": 100, "value": 5}],
        "total": 3,
        "truncated": False,
        "candidates": 3,
    }
    client = _FakeClient(response)
    result = _mem_diff(client, {"handle": 1, "op": "changed"})
    body = json.loads(result[0].text)
    assert body["total"] == 3
    assert body["candidates"] == 3
    assert body["matches"][0]["addr"] == 100


# ---------------------------------------------------------------------------
# mem_alloc / mem_free / mem_allocations
# ---------------------------------------------------------------------------


def test_mem_alloc_posts_size_only_when_area_and_strategy_omitted():
    client = _FakeClient({"addr": 1048576})
    _mem_alloc(client, {"size": 4096})
    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/allocate"
    assert client.last_kwargs["json"] == {"size": 4096}


def test_mem_alloc_passes_area_and_strategy_through_when_given():
    client = _FakeClient({"addr": 1048576})
    _mem_alloc(client, {"size": 4096, "area": "XMS", "strategy": "BEST_FIT"})
    assert client.last_kwargs["json"] == {
        "size": 4096, "area": "XMS", "strategy": "BEST_FIT",
    }


def test_mem_alloc_returns_the_allocated_address():
    client = _FakeClient({"addr": 1048576})
    result = _mem_alloc(client, {"size": 4096})
    body = json.loads(result[0].text)
    assert body["addr"] == 1048576


def test_mem_alloc_propagates_a_dosbox_error_on_failure():
    # A 503 (registry_full or insufficient_memory): DosboxClient._handle
    # raises DosboxError on any >=400 response - the handler must not
    # swallow it (e.g. by wrapping client.post in a try/except the way
    # _mem_write handles its own 412 conflict), or a real allocation
    # failure would surface as fabricated success instead of isError.
    client = _FakeClient()
    client.post = lambda *a, **k: (_ for _ in ()).throw(
        DosboxError(503, "insufficient_memory",
                    "insufficient free memory for this allocation"))
    with pytest.raises(DosboxError):
        _mem_alloc(client, {"size": 65535})


def test_mem_free_posts_addr():
    client = _FakeClient(b"")
    _mem_free(client, {"addr": 1048576})
    assert client.last_method == "post"
    assert client.last_path == "/api/v1/memory/free"
    assert client.last_kwargs["json"] == {"addr": 1048576}


def test_mem_free_reports_ok_on_the_engines_empty_success_body():
    # POST /memory/free returns 200 with no body on success (the engine
    # never calls send_json on that path) - the handler must not choke
    # on that, or assume the empty bytes/response object is JSON.
    client = _FakeClient(b"")
    result = _mem_free(client, {"addr": 1048576})
    body = json.loads(result[0].text)
    assert body == {"status": "ok"}


def test_mem_free_propagates_a_dosbox_error_on_failure():
    # An untracked/already-freed addr: DosboxClient._handle raises
    # DosboxError on any >=400 response - the handler must not swallow
    # it, so guard() can turn it into an isError result upstream.
    client = _FakeClient()
    client.post = lambda *a, **k: (_ for _ in ()).throw(
        DosboxError(400, "invalid_argument", "addr was not allocated "
                                             "through this API"))
    with pytest.raises(DosboxError):
        _mem_free(client, {"addr": 1048576})


def test_mem_allocations_gets_with_no_params():
    client = _FakeClient({"allocations": [], "conventionalFreeBytes": 0,
                          "conventionalLargestBlockBytes": 0,
                          "conventionalTruncated": False,
                          "umbFreeBytes": 0, "umbTruncated": False,
                          "xmsFreeBytes": 0})
    _mem_allocations(client)
    assert client.last_method == "get"
    assert client.last_path == "/api/v1/memory/allocations"


def test_mem_allocations_returns_the_full_response():
    response = {
        "allocations": [{"addr": 1048576, "size": 4096, "area": "CONV"}],
        "conventionalFreeBytes": 634880,
        "conventionalLargestBlockBytes": 634880,
        "conventionalTruncated": False,
        "umbFreeBytes": 0,
        "umbTruncated": False,
        "xmsFreeBytes": 66060288,
    }
    client = _FakeClient(response)
    result = _mem_allocations(client)
    body = json.loads(result[0].text)
    assert body == response
