"""designed_par sets the EFF par WITHOUT opting into golf scoring.

confirmation-desk v2 turned six get_confirmation_batch `expected_tools` into
assertion_any_of checks, so its derived par fell 9 -> 3. `par_tool_calls: 9`
would restore the number but also mark the par calibrated (golf EFF, zero at 18
calls — below every model's real count). `designed_par` keeps the legacy curve.
"""
from types import SimpleNamespace

from app.golden_workflows.registry import get_workflow_bundle
from app.services.arena import scoring


def _wf(**kw):
    steps = [SimpleNamespace(expected_tools=[1, 2]), SimpleNamespace(expected_tools=[3])]
    return SimpleNamespace(steps=steps, par_tool_calls=kw.get("par"), designed_par=kw.get("designed"))


def test_precedence_calibrated_then_designed_then_derived():
    assert scoring.designed_par(_wf()) == 3
    assert scoring.designed_par(_wf(designed=9)) == 9
    assert scoring.designed_par(_wf(designed=9, par=24)) == 24


def test_designed_par_does_not_calibrate():
    assert not scoring.par_calibrated(_wf(designed=9))
    assert scoring.par_calibrated(_wf(par=9))


def test_confirmation_desk_keeps_par_9_on_the_legacy_curve():
    wf = get_workflow_bundle("confirmation-desk-day").workflow
    assert scoring.designed_par(wf) == 9
    assert not scoring.par_calibrated(wf)
