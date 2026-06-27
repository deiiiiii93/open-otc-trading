import pytest
from app.services.desk_workflows_script import (
    WorkflowScriptError,
    extract_meta,
    extract_slug,
    guard_script,
    validate_params,
    validate_script,
)

GOOD = (
    'meta = {"name": "x", "title": "X", "persona": "trader", '
    '"mode": "auto", "scope": "local"}\n'
    'await step("hello")\n'
)


def test_extract_meta_ok():
    assert extract_meta(GOOD)["name"] == "x"


def test_extract_meta_missing():
    with pytest.raises(WorkflowScriptError):
        extract_meta('await step("hi")\n')


def test_extract_meta_non_literal():
    with pytest.raises(WorkflowScriptError):
        extract_meta('meta = dict(name="x")\nawait step("hi")\n')


def test_guard_rejects_import():
    with pytest.raises(WorkflowScriptError):
        guard_script('import os\nawait step("hi")\n')


def test_guard_rejects_dunder():
    with pytest.raises(WorkflowScriptError):
        guard_script('x = ().__class__\nawait step("hi")\n')


def test_guard_rejects_format_dunder_bypass():
    # str.format reaches __class__ via a string literal (no ast.Attribute node).
    with pytest.raises(WorkflowScriptError):
        guard_script('x = "{0.__class__}".format(())\nawait step("hi")\n')


def test_guard_rejects_mro():
    with pytest.raises(WorkflowScriptError):
        guard_script('x = type.mro\nawait step("hi")\n')


def test_validate_slug_mismatch():
    with pytest.raises(WorkflowScriptError):
        validate_script(GOOD, slug="other")


def test_validate_ok():
    meta = validate_script(GOOD, slug="x")
    assert meta["persona"] == "trader" and meta["mode"] == "auto"


def test_validate_rejects_unhashable_meta_value():
    # An unhashable value (list) for a string field must not raise TypeError.
    bad = (
        'meta = {"name": "x", "title": "X", "persona": [], '
        '"mode": "auto", "scope": "local"}\n'
        'await step("hi")\n'
    )
    with pytest.raises(WorkflowScriptError):
        validate_script(bad, slug="x")


def test_validate_bad_enum():
    bad = GOOD.replace('"persona": "trader"', '"persona": "wizard"')
    with pytest.raises(WorkflowScriptError):
        validate_script(bad, slug="x")


def test_extract_slug_missing_name():
    with pytest.raises(WorkflowScriptError):
        extract_slug('meta = {"title": "X"}\nawait step("hi")\n')


def test_validate_rejects_unsafe_slug():
    bad = (
        'meta = {"name": "a/b", "title": "X", "persona": "trader", '
        '"mode": "auto", "scope": "local"}\n'
        'await step("hi")\n'
    )
    with pytest.raises(WorkflowScriptError):
        validate_script(bad, slug="a/b")


def test_validate_reserved_slug():
    reserved = (
        'meta = {"name": "goal", "title": "G", "persona": "trader", '
        '"mode": "auto", "scope": "local"}\n'
        'await step("hi")\n'
    )
    with pytest.raises(WorkflowScriptError):
        validate_script(reserved, slug="goal")


def _meta(params):
    return {
        "name": "x", "title": "X", "persona": "trader",
        "mode": "auto", "scope": "local", "params": params,
    }


def test_validate_params_absent_returns_empty():
    assert validate_params({"name": "x"}) == []


def test_validate_params_happy_normalizes():
    out = validate_params(_meta([
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Start date", "type": "date"},
    ]))
    assert out == [
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Start date", "type": "date"},
    ]


@pytest.mark.parametrize("params", [
    "notalist",
    [["not", "a", "dict"]],
    [{"label": "L", "type": "string"}],                    # missing name
    [{"name": "p", "type": "string"}],                     # missing label
    [{"name": "p", "label": "L"}],                         # missing type
    [{"name": "p", "label": "L", "type": "color"}],        # bad type
    [{"name": "Portfolio", "label": "L", "type": "string"}],   # uppercase
    [{"name": "portfolio name", "label": "L", "type": "string"}],  # space
    [{"name": "for", "label": "L", "type": "string"}],     # python keyword
    [{"name": "args", "label": "L", "type": "string"}],    # reserved
    [{"name": "p", "label": "L", "type": "string"},
     {"name": "p", "label": "L2", "type": "date"}],        # duplicate
])
def test_validate_params_rejects(params):
    with pytest.raises(WorkflowScriptError):
        validate_params(_meta(params))
