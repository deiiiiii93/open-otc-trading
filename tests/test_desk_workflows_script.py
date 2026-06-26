import pytest
from app.services.desk_workflows_script import (
    WorkflowScriptError,
    extract_meta,
    guard_script,
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


def test_validate_bad_enum():
    bad = GOOD.replace('"persona": "trader"', '"persona": "wizard"')
    with pytest.raises(WorkflowScriptError):
        validate_script(bad, slug="x")


def test_validate_reserved_slug():
    reserved = (
        'meta = {"name": "goal", "title": "G", "persona": "trader", '
        '"mode": "auto", "scope": "local"}\n'
        'await step("hi")\n'
    )
    with pytest.raises(WorkflowScriptError):
        validate_script(reserved, slug="goal")
