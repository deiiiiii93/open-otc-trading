"""RFQ domain service.

Thin facade over ``services/rfq.py`` and ``services/quantark.solve_rfq``. The
existing implementation in ``services/rfq.py`` already encapsulates the full
RFQ lifecycle (draft -> quote -> approve -> release -> client_accepted ->
booked); this module re-exports the lifecycle functions under
``services.domains.rfq`` so tools, CLI, and API layers have a single domain
import path.

Session management note: most underlying functions require a SQLAlchemy
``Session`` as the first argument. The tools layer (``app.tools.rfq``)
opens and commits its own session; callers of this facade are expected to
manage sessions explicitly. This matches the pattern in
``services/rfq.py`` and avoids hiding transaction boundaries.
"""
from __future__ import annotations

from app.services import rfq as _rfq
from app.services.quantark import solve_rfq as _solve_rfq

# Stateless / pure functions
get_rfq_catalog = _rfq.get_rfq_catalog
draft_from_natural_language = _rfq.draft_from_natural_language
validate_rfq_terms = _rfq.validate_rfq_terms
solve_rfq = _solve_rfq

# Session-dependent lifecycle operations
create_rfq_draft = _rfq.create_rfq_draft
update_rfq_draft = _rfq.update_rfq_draft
submit_rfq_for_approval = _rfq.submit_rfq_for_approval
quote_rfq = _rfq.quote_rfq
approve_rfq = _rfq.approve_rfq
reject_rfq = _rfq.reject_rfq
release_rfq = _rfq.release_rfq
mark_client_accepted = _rfq.mark_client_accepted
book_rfq_to_position = _rfq.book_rfq_to_position
latest_quote_version = _rfq.latest_quote_version

__all__ = [
    "get_rfq_catalog",
    "draft_from_natural_language",
    "validate_rfq_terms",
    "solve_rfq",
    "create_rfq_draft",
    "update_rfq_draft",
    "submit_rfq_for_approval",
    "quote_rfq",
    "approve_rfq",
    "reject_rfq",
    "release_rfq",
    "mark_client_accepted",
    "book_rfq_to_position",
    "latest_quote_version",
]
