"""The lifecycle event vocabulary: its shape, its guards, and its retirements."""
from __future__ import annotations

from app.services.domains import lifecycle_vocabulary as vocab
from app.services.domains import positions as positions_svc


def test_positions_still_re_exports_the_vocabulary():
    """positions.py is the historical import site; tests and the settlement
    deriver read the maps from there, so the move must not break them."""
    assert positions_svc.LIFECYCLE_EVENT_TARGETS is vocab.LIFECYCLE_EVENT_TARGETS
    assert positions_svc.PRODUCT_LIFECYCLE_EVENTS is vocab.PRODUCT_LIFECYCLE_EVENTS
    assert positions_svc.valid_lifecycle_event_types is vocab.valid_lifecycle_event_types


def test_vocabulary_module_imports_nothing_from_the_position_service():
    """The vocabulary is pure data. Keeping it free of service imports is what
    lets the router, the deriver and the tests read it without dragging in the
    DB layer."""
    import inspect

    source = inspect.getsource(vocab)
    assert "from .positions" not in source
    assert "import database" not in source


def test_every_declared_event_type_is_reachable_or_explicitly_retired():
    """A type nobody can record is a silent capability deletion, not a warning.

    This is the test that would have caught the dead `open`/`reopen` types and
    the `premium` cash leg that could never fire. The RETIRED escape hatch is
    what keeps a deliberate retirement distinguishable from an accidental
    orphan: it costs one line, and that line is the decision record.
    """
    reachable = set().union(*vocab.PRODUCT_LIFECYCLE_EVENTS.values())
    orphans = set(vocab.LIFECYCLE_EVENT_TARGETS) - reachable - vocab.RETIRED_EVENT_TYPES
    assert orphans == set(), f"declared but unreachable: {sorted(orphans)}"


def test_every_cash_leg_rule_is_reachable_or_explicitly_retired():
    """A cash rule keyed on an unreachable event books money nobody can trigger."""
    from app.services.settlement.derive import CASH_LEG_RULES

    reachable = set().union(*vocab.PRODUCT_LIFECYCLE_EVENTS.values())
    orphans = set(CASH_LEG_RULES) - reachable - vocab.RETIRED_EVENT_TYPES
    assert orphans == set(), f"cash rules that can never fire: {sorted(orphans)}"


def test_every_bookable_family_has_an_explicit_event_map():
    """Falling through to a default is how KnockOutResetSnowballOption ended up
    a snowball with no knock-in, knock-out or coupon events."""
    from app.services.domains.product_builders import _REGISTRY

    missing = set(_REGISTRY) - set(vocab.PRODUCT_LIFECYCLE_EVENTS)
    assert missing == set(), f"families with no event map: {sorted(missing)}"


def test_retired_types_keep_their_target_status_and_cash_rule():
    """Retired means no NEW events, not that the system forgets what the type
    meant. `_project_status_from_lifecycle` resolves targets with `.get()`, so
    deleting `autocall` would make a historical phoenix autocall read as
    non-transitioning and silently stop projecting the position to `closed`.
    """
    from app.services.settlement.derive import CASH_LEG_RULES

    assert vocab.RETIRED_EVENT_TYPES == frozenset({"autocall", "coupon_lock"})
    assert vocab.LIFECYCLE_EVENT_TARGETS["autocall"] == "closed"
    assert vocab.LIFECYCLE_EVENT_TARGETS["coupon_lock"] is None
    assert "autocall" in CASH_LEG_RULES
    for retired in vocab.RETIRED_EVENT_TYPES:
        for family, allowed in vocab.PRODUCT_LIFECYCLE_EVENTS.items():
            assert retired not in allowed, f"{family} may still record {retired}"


def test_every_family_can_record_the_universal_base_events():
    assert vocab.BASE_EVENTS == frozenset(
        {"open", "reopen", "close", "settle", "maturity", "custom"}
    )
    for family, allowed in vocab.PRODUCT_LIFECYCLE_EVENTS.items():
        assert vocab.BASE_EVENTS <= allowed, f"{family} is missing base events"


def test_one_touch_models_the_touch_as_a_terminating_knock_out():
    """The touch both ends the option and creates the obligation. `knock_in`
    targets `knocked_in` and has no cash rule at all, which would leave the
    money invisible until maturity."""
    for family in ("OneTouchOption", "DoubleOneTouchOption"):
        allowed = vocab.PRODUCT_LIFECYCLE_EVENTS[family]
        assert "knock_out" in allowed
        assert "knock_in" not in allowed


def test_ko_reset_snowball_is_a_snowball_plus_its_stepping_barrier():
    snowball = vocab.PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    ko_reset = vocab.PRODUCT_LIFECYCLE_EVENTS["KnockOutResetSnowballOption"]
    assert ko_reset == snowball | {"barrier_reset"}


def test_barrier_reset_and_expire_do_not_move_status_or_book_cash():
    from app.services.settlement.derive import CASH_LEG_RULES

    assert vocab.LIFECYCLE_EVENT_TARGETS["barrier_reset"] is None
    assert vocab.LIFECYCLE_EVENT_TARGETS["expire"] == "closed"
    assert "barrier_reset" not in CASH_LEG_RULES
    assert "expire" not in CASH_LEG_RULES
