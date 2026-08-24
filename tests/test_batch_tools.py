# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import jsonschema
import pytest

from dosbox_mcp.tools.batch import MAX_BATCH_OPS, _batch_execute


class _FakeClient:
    def __init__(self, response=None):
        self._response = response if response is not None else {}
        self.last_path = None
        self.last_json = None

    def post(self, path, json=None):
        self.last_path = path
        self.last_json = json
        return self._response


def _schema():
    from dosbox_mcp.tools import batch

    captured = {}

    def add_tool(name, schema=None, **kwargs):
        if name == "batch_execute":
            captured["schema"] = schema

    batch.register(server=None, client=None, add_tool=add_tool)
    return captured["schema"]


# ---------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------


def test_ops_are_forwarded_unchanged():
    client = _FakeClient()
    ops = [{"op": "cpu_read"}, {"op": "port_read", "port": 0x60}]
    _batch_execute(client, {"ops": ops})

    assert client.last_path == "/api/v1/batch"
    assert client.last_json == {"ops": ops}


def test_on_error_is_forwarded_only_when_given():
    client = _FakeClient()
    _batch_execute(client, {"ops": [{"op": "cpu_read"}]})
    assert "on_error" not in client.last_json

    _batch_execute(client, {"ops": [{"op": "cpu_read"}], "on_error": "continue"})
    assert client.last_json["on_error"] == "continue"


def test_result_is_the_raw_engine_response():
    response = {
        "results": [{"op": "cpu_read", "status": "ok", "registers": {}}],
        "aborted": False,
    }
    client = _FakeClient(response)
    result = _batch_execute(client, {"ops": [{"op": "cpu_read"}]})

    import json

    assert json.loads(result[0].text) == response


# ---------------------------------------------------------------------
# Schema: every op branch, checked against the real registered schema
# ---------------------------------------------------------------------


def test_schema_rejects_an_empty_ops_array():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": []}, _schema())


def test_schema_rejects_more_than_max_batch_ops():
    ops = [{"op": "cpu_read"}] * (MAX_BATCH_OPS + 1)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": ops}, _schema())


def test_schema_accepts_exactly_max_batch_ops():
    ops = [{"op": "cpu_read"}] * MAX_BATCH_OPS
    jsonschema.validate({"ops": ops}, _schema())


def test_schema_rejects_an_unknown_op():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [{"op": "debug_pause"}]}, _schema())


def test_schema_rejects_an_unknown_top_level_property():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"ops": [{"op": "cpu_read"}], "bogus": 1}, _schema()
        )


def test_schema_rejects_an_unknown_on_error_value():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"ops": [{"op": "cpu_read"}], "on_error": "retry"}, _schema()
        )


@pytest.mark.parametrize(
    "op",
    [
        {"op": "mem_read", "offset": 0, "len": 16},
        {"op": "mem_read", "segment": "ds", "offset": 0, "len": 16},
        {"op": "mem_read", "segment": 0x1234, "offset": 0, "len": 16},
        {"op": "mem_write", "offset": 0, "data": "AA=="},
        {"op": "mem_cas", "offset": 0, "data": "AA==", "expected": "AA=="},
        {"op": "cpu_read"},
        {"op": "cpu_write", "register": "eax", "value": 1},
        {"op": "cpu_write", "register": "ds", "value": 0x1234},
        {"op": "port_read", "port": 0x60},
        {"op": "port_read", "port": 0x60, "width": 2},
        {"op": "port_write", "port": 0x60, "value": 1},
        {"op": "freeze_set", "address": 100, "value": 1},
        {"op": "freeze_set", "address": 100, "value": 1, "width": 4},
        {"op": "freeze_clear", "address": 100},
    ],
)
def test_schema_accepts_every_valid_op_shape(op):
    jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_mem_write_with_an_extra_expected_field():
    # additionalProperties:False on the mem_write branch - expected only
    # belongs to mem_cas.
    op = {"op": "mem_write", "offset": 0, "data": "AA==", "expected": "AA=="}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_mem_cas_without_expected():
    op = {"op": "mem_cas", "offset": 0, "data": "AA=="}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_accepts_a_segment_register_value_above_0xffff():
    # Intentional, not an oversight: the engine rejects a segment
    # register value over 0xFFFF (verified in
    # tests/webserver_batch_tests.cpp's CpuWriteRejectsAnOversizedSegmentValue),
    # but this schema - like cpu_write_register's own single-op schema
    # in tools/cpu.py - applies one generic 0..0xFFFFFFFF bound to
    # 'value' regardless of which register is named, since the schema
    # is built once at startup with no way to know per-op which class a
    # future 'register' value belongs to. The engine has final say.
    op = {"op": "cpu_write", "register": "ds", "value": 0x10000}
    jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_cpu_write_with_an_unknown_register():
    op = {"op": "cpu_write", "register": "xax", "value": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_an_out_of_range_port():
    op = {"op": "port_read", "port": 0x10000}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_an_invalid_port_width():
    op = {"op": "port_write", "port": 0x60, "value": 1, "width": 4}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_an_invalid_freeze_width():
    op = {"op": "freeze_set", "address": 0, "value": 1, "width": 3}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_a_freeze_set_missing_value():
    op = {"op": "freeze_set", "address": 0}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [op]}, _schema())


def test_schema_rejects_an_op_object_with_no_op_field():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [{"offset": 0}]}, _schema())


def test_schema_rejects_a_cpu_read_with_extra_fields():
    # cpu_read's branch has no properties beyond 'op' -
    # additionalProperties:False must reject anything else.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ops": [{"op": "cpu_read", "value": 1}]}, _schema())
