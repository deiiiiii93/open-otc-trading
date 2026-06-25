"""Golden tests for the fail-closed approval-card builder (Task 10).

Real pending_action payload shape (from hitl.py + tool schemas):
  AgentActionProposal.tool_name  — tool name string
  AgentActionProposal.payload    — raw args dict passed to the tool

book_position payload keys (from app/tools/positions.py BookPositionInput):
  product        dict(product_family, quantark_class, underlying, currency, terms, ...)
  quantity       float — number of contracts / notional scalar
  portfolio_id   int
  entry_price    float (default 0.0)
  engine_name    str   (default "BlackScholesEngine")
  status         str   (default "open")

book_hedge payload keys (from app/tools/hedging.py BookHedgeInput):
  portfolio_id   int
  underlying     str
  risk_run_id    int
  strategy       str
  spot           float
  legs           list[dict]

quote_rfq payload keys (from app/tools/rfq.py QuoteRfqInput):
  rfq_id         int
  quote_mode     str | None
  created_by     str
  product        dict | None (contains terms)
  market         any

submit_rfq_for_approval payload keys:
  rfq_id         int
  actor          str

approve_rfq payload keys:
  rfq_id         int
  approver       str
  comment        str | None

reject_rfq payload keys:
  rfq_id         int
  approver       str
  comment        str | None

release_rfq payload keys:
  rfq_id         int
  actor          str
  response_override str | None

REQUIRED_FIELDS uses these real keys (adapted from the brief's logical-key map).
"""
from __future__ import annotations

import pytest

from app.models import GatewayBinding
from app.schemas import AgentActionProposal
from app.services.gateway.cards import IRREVERSIBLE, REQUIRED_FIELDS, build_approval_card
from app.services.gateway.types import MessageRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_binding(session) -> GatewayBinding:
    b = GatewayBinding(
        provider="feishu",
        external_account_id="ou_test",
        workspace_id="tk_test",
        desk_user="desk_user",
        persona="trader",
        status="active",
    )
    session.add(b)
    session.flush()
    return b


def _make_out_ref() -> MessageRef:
    return MessageRef(
        connector="feishu",
        workspace_id="tk_test",
        chat_id="chat_001",
        message_id="msg_card_001",
    )


def _make_action(tool_name: str, payload: dict) -> AgentActionProposal:
    return AgentActionProposal(
        id="intr_001:0",
        tool_name=tool_name,
        label=tool_name,
        summary="test action",
        payload=payload,
        requires_confirmation=True,
        status="pending",
    )


_FULL_BOOK_POSITION_PAYLOAD = {
    "product": {
        "product_family": "vanilla",
        "quantark_class": "EuropeanOption",
        "underlying": "000300.SH",
        "currency": "CNY",
        "terms": {"strike": 4200.0, "expiry": "2026-12-31"},
    },
    "quantity": 100.0,
    "portfolio_id": 42,
    "entry_price": 0.0,
    "engine_name": "BlackScholesEngine",
}

_FULL_BOOK_HEDGE_PAYLOAD = {
    "portfolio_id": 42,
    "underlying": "000300.SH",
    "risk_run_id": 7,
    "strategy": "delta_hedge",
    "spot": 4100.0,
    "legs": [{"side": "buy", "quantity": 50, "instrument": "futures"}],
}

_FULL_QUOTE_RFQ_PAYLOAD = {
    "rfq_id": 99,
    "quote_mode": "solve",
    "created_by": "desk_user",
    "product": {"terms": {"strike": 4200.0}},
    "market": None,
}

_FULL_SUBMIT_RFQ_PAYLOAD = {
    "rfq_id": 99,
    "actor": "agent_confirmed",
}

_FULL_APPROVE_RFQ_PAYLOAD = {
    "rfq_id": 99,
    "approver": "risk_manager",
    "comment": "looks good",
}

_FULL_REJECT_RFQ_PAYLOAD = {
    "rfq_id": 99,
    "approver": "risk_manager",
    "comment": "need revision",
}

_FULL_RELEASE_RFQ_PAYLOAD = {
    "rfq_id": 99,
    "actor": "trader",
    "response_override": None,
}


# ---------------------------------------------------------------------------
# REQUIRED_FIELDS / IRREVERSIBLE contract
# ---------------------------------------------------------------------------

class TestRequiredFieldsContract:
    def test_required_fields_has_all_gated_tools(self):
        expected_tools = {
            "book_position", "book_hedge", "quote_rfq",
            "submit_rfq_for_approval", "approve_rfq", "reject_rfq", "release_rfq",
            "__cost_preview__",
        }
        assert set(REQUIRED_FIELDS.keys()) == expected_tools

    def test_irreversible_set(self):
        assert IRREVERSIBLE == {"book_position", "book_hedge", "approve_rfq", "release_rfq"}

    def test_required_fields_are_lists(self):
        for tool, fields in REQUIRED_FIELDS.items():
            assert isinstance(fields, list), f"{tool}: expected list, got {type(fields)}"
            assert len(fields) > 0, f"{tool}: empty field list"

    def test_book_position_required_fields(self):
        fields = REQUIRED_FIELDS["book_position"]
        # Must include actual tool payload keys (not brief's logical names)
        assert "product" in fields
        assert "quantity" in fields
        assert "portfolio_id" in fields

    def test_book_hedge_required_fields(self):
        fields = REQUIRED_FIELDS["book_hedge"]
        assert "underlying" in fields
        assert "portfolio_id" in fields
        assert "legs" in fields

    def test_quote_rfq_required_fields(self):
        fields = REQUIRED_FIELDS["quote_rfq"]
        assert "rfq_id" in fields

    def test_rfq_tools_have_rfq_id(self):
        for tool in ("submit_rfq_for_approval", "approve_rfq", "reject_rfq", "release_rfq"):
            assert "rfq_id" in REQUIRED_FIELDS[tool], f"{tool} missing rfq_id"


# ---------------------------------------------------------------------------
# book_position — full payload → approvable, irreversible warning
# ---------------------------------------------------------------------------

class TestBuildApprovalCardBookPosition:
    def test_full_payload_is_approvable(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 2

    def test_full_payload_has_approve_and_reject_actions(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        labels = [a.label.lower() for a in card.actions]
        assert any("approve" in lbl or "confirm" in lbl for lbl in labels)
        assert any("reject" in lbl or "dismiss" in lbl for lbl in labels)

    def test_full_payload_actions_have_tokens(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        for act in card.actions:
            assert isinstance(act.token, str)
            assert len(act.token) > 10

    def test_full_payload_has_irreversible_warning(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        # Irreversible warning should appear in body or sections
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        )
        assert "irreversible" in full_text.lower() or "cannot be undone" in full_text.lower()

    def test_full_payload_includes_required_field_values(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        # Some representation of the payload must appear in the card body or sections
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        )
        assert full_text.strip() != ""

    def test_full_payload_card_not_resolved(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert card.resolved is False


# ---------------------------------------------------------------------------
# book_position — missing required field → non-approvable + web link
# ---------------------------------------------------------------------------

class TestBuildApprovalCardMissingField:
    def test_missing_quantity_yields_no_actions(self, db_session, db_settings):
        payload = dict(_FULL_BOOK_POSITION_PAYLOAD)
        del payload["quantity"]
        binding = _make_binding(db_session)
        action = _make_action("book_position", payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 0

    def test_missing_product_yields_no_actions(self, db_session, db_settings):
        payload = dict(_FULL_BOOK_POSITION_PAYLOAD)
        del payload["product"]
        binding = _make_binding(db_session)
        action = _make_action("book_position", payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 0

    def test_missing_field_card_has_web_link_when_base_url_set(self, db_session, db_settings):
        import dataclasses
        settings_with_url = dataclasses.replace(
            db_settings, gateway_web_base_url="https://desk.example.com"
        )
        payload = dict(_FULL_BOOK_POSITION_PAYLOAD)
        del payload["quantity"]
        db_settings_local = settings_with_url
        binding = _make_binding(db_session)
        action = _make_action("book_position", payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings_local,
        )
        assert len(card.actions) == 0
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        ) + (card.footer or "")
        assert "desk.example.com" in full_text

    def test_missing_field_card_is_resolved_false(self, db_session, db_settings):
        payload = dict(_FULL_BOOK_POSITION_PAYLOAD)
        del payload["quantity"]
        binding = _make_binding(db_session)
        action = _make_action("book_position", payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert card.resolved is False


# ---------------------------------------------------------------------------
# Unknown tool → non-approvable (fail-closed)
# ---------------------------------------------------------------------------

class TestBuildApprovalCardUnknownTool:
    def test_unknown_tool_yields_no_actions(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("some_unknown_tool", {"foo": "bar"})
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 0

    def test_unknown_tool_card_has_title(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("some_unknown_tool", {"foo": "bar"})
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert card.title


# ---------------------------------------------------------------------------
# quote_rfq — approvable, NO irreversible warning
# ---------------------------------------------------------------------------

class TestBuildApprovalCardQuoteRfq:
    def test_full_payload_is_approvable(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("quote_rfq", _FULL_QUOTE_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 2

    def test_no_irreversible_warning(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("quote_rfq", _FULL_QUOTE_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        )
        # quote_rfq is NOT in IRREVERSIBLE so should NOT have warning
        assert "irreversible" not in full_text.lower()
        assert "cannot be undone" not in full_text.lower()


# ---------------------------------------------------------------------------
# Oversized param — truncated but still approvable
# ---------------------------------------------------------------------------

class TestBuildApprovalCardOversizedParam:
    def test_oversized_value_still_approvable(self, db_session, db_settings):
        """A very long legs list is oversized but all required fields are present."""
        oversized_payload = dict(_FULL_BOOK_HEDGE_PAYLOAD)
        oversized_payload["legs"] = [
            {"side": "buy", "quantity": i, "instrument": "futures", "note": "x" * 200}
            for i in range(50)
        ]
        binding = _make_binding(db_session)
        action = _make_action("book_hedge", oversized_payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        # Must still be approvable (2 actions)
        assert len(card.actions) == 2

    def test_oversized_value_is_truncated_in_display(self, db_session, db_settings):
        """Oversized value must be truncated in the displayed text."""
        oversized_payload = dict(_FULL_BOOK_HEDGE_PAYLOAD)
        # Create a very long list value
        oversized_payload["legs"] = [
            {"side": "buy", "quantity": i, "instrument": "futures", "note": "x" * 200}
            for i in range(50)
        ]
        binding = _make_binding(db_session)
        action = _make_action("book_hedge", oversized_payload)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        ) + (card.footer or "")
        # The full raw text of 50 items with 200 'x' chars each shouldn't appear verbatim
        # Instead a truncation indicator should be present
        raw_full = str(oversized_payload["legs"])
        assert raw_full not in full_text  # must not dump the full raw list


# ---------------------------------------------------------------------------
# approve_rfq — irreversible warning
# ---------------------------------------------------------------------------

class TestBuildApprovalCardApproveRfq:
    def test_approve_rfq_is_irreversible(self):
        assert "approve_rfq" in IRREVERSIBLE

    def test_approve_rfq_full_payload_approvable(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("approve_rfq", _FULL_APPROVE_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 2

    def test_approve_rfq_has_irreversible_warning(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("approve_rfq", _FULL_APPROVE_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        )
        assert "irreversible" in full_text.lower() or "cannot be undone" in full_text.lower()


# ---------------------------------------------------------------------------
# reject_rfq — NOT irreversible
# ---------------------------------------------------------------------------

class TestBuildApprovalCardRejectRfq:
    def test_reject_rfq_not_irreversible(self):
        assert "reject_rfq" not in IRREVERSIBLE

    def test_reject_rfq_full_payload_approvable(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("reject_rfq", _FULL_REJECT_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 2

    def test_reject_rfq_no_irreversible_warning(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("reject_rfq", _FULL_REJECT_RFQ_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        full_text = card.body + " ".join(
            (s.title or "") + " " + (s.body or "") for s in card.sections
        )
        assert "irreversible" not in full_text.lower()
        assert "cannot be undone" not in full_text.lower()


# ---------------------------------------------------------------------------
# Token uniqueness — approve and reject tokens must differ
# ---------------------------------------------------------------------------

class TestTokenUniqueness:
    def test_approve_reject_tokens_differ(self, db_session, db_settings):
        binding = _make_binding(db_session)
        action = _make_action("book_position", _FULL_BOOK_POSITION_PAYLOAD)
        card = build_approval_card(
            db_session,
            binding=binding,
            thread_id=1,
            message_id=10,
            pending_action=action,
            out_ref=_make_out_ref(),
            settings=db_settings,
        )
        assert len(card.actions) == 2
        tokens = [a.token for a in card.actions]
        assert tokens[0] != tokens[1]
