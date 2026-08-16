"""_editable_fields_for must derive a static select from the tool's own pinned
input_schema enum when the FieldSpec declares neither control nor options —
found in prod: update_inspection_status.status enum[pass,repair,fail] rendered
as a free-text box because the builder omitted options the spec already knew.
Builder-authored options/control always win; derivation only fills the gap.
"""
from types import SimpleNamespace

from models import FieldSpec, OptionsSource, OptionItem
from runtime import _editable_fields_for


def _tool(editable_fields, input_schema):
    return SimpleNamespace(
        kind="mcp_action", action_id="update_inspection_status",
        source_id="field_operations", dataset_id="field_operations.equipment_inspections",
        editable_fields=editable_fields, input_schema=input_schema,
    )


SCHEMA = {"type": "object", "required": ["inspection_id", "status"],
          "properties": {"inspection_id": {"type": "string"},
                         "status": {"type": "string", "enum": ["pass", "repair", "fail"]}}}


def _agent(tool):
    return SimpleNamespace(tools_v2=[tool])


def test_enum_derives_static_select():
    tool = _tool([FieldSpec(name="status", label="Outcome", editable=True)], SCHEMA)
    out = _editable_fields_for(_agent(tool), "field_operations",
                               "field_operations.equipment_inspections",
                               "update_inspection_status")
    assert len(out) == 1
    f = out[0]
    assert f["control"] == "select"
    assert f["options"]["kind"] == "static"
    assert [v["value"] for v in f["options"]["values"]] == ["pass", "repair", "fail"]
    assert f["options"]["values"][0]["label"] == "Pass"


def test_builder_authored_options_win():
    authored = FieldSpec(name="status", label="Outcome", control="select",
                         options=OptionsSource(kind="static",
                                               values=[OptionItem(value="pass", label="OK")]))
    tool = _tool([authored], SCHEMA)
    out = _editable_fields_for(_agent(tool), None, None, "update_inspection_status")
    assert [v["value"] for v in out[0]["options"]["values"]] == ["pass"]  # untouched


def test_no_enum_stays_free_text():
    tool = _tool([FieldSpec(name="note", label="Note", editable=True)], SCHEMA)
    out = _editable_fields_for(_agent(tool), None, None, "update_inspection_status")
    assert "options" not in out[0] and "control" not in out[0]


def test_explicit_control_not_overridden():
    tool = _tool([FieldSpec(name="status", control="text")], SCHEMA)
    out = _editable_fields_for(_agent(tool), None, None, "update_inspection_status")
    assert out[0]["control"] == "text" and "options" not in out[0]


def test_linter_flags_enum_field_without_options():
    # the publish linter must surface the omission so hand-authored specs
    # (like the prod one) get told at publish time, not found in the modal.
    from main import _lint_app_spec

    app = SimpleNamespace(data_sources=[], all_panels=[])
    tool = _tool([FieldSpec(name="status", label="Outcome", editable=True)], SCHEMA)
    agent = SimpleNamespace(tools_v2=[tool], outcome_poll=None)
    codes = {f["code"] for f in _lint_app_spec(app, agent)}
    assert "enum_field_missing_options" in codes

    # declared options -> no finding
    ok_tool = _tool([FieldSpec(name="status", control="select",
                               options=OptionsSource(kind="static",
                                                     values=[OptionItem(value="pass")]))], SCHEMA)
    agent2 = SimpleNamespace(tools_v2=[ok_tool], outcome_poll=None)
    codes2 = {f["code"] for f in _lint_app_spec(app, agent2)}
    assert "enum_field_missing_options" not in codes2
