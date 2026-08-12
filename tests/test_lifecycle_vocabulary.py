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
