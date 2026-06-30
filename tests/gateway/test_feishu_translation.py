"""Tests for Feishu v2 event/card translation pure functions."""
from __future__ import annotations

import json
import pytest

from app.services.gateway.connectors.feishu import (
    feishu_event_to_inbound,
    feishu_card_action_to_inbound,
    outbound_card_to_feishu,
)
from app.services.gateway.types import CardAction, CardSection, OutboundCard

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MESSAGE_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_001",
        "event_type": "im.message.receive_v1",
        "tenant_key": "TENANT_01",
        "app_id": "cli_abc",
        "create_time": "1623xxx",
        "token": "VTOKEN",
    },
    "event": {
        "sender": {
            "sender_id": {
                "open_id": "ou_user_001",
                "union_id": "on_xxx",
                "user_id": "usr_xxx",
            },
            "sender_type": "user",
        },
        "message": {
            "message_id": "om_msg_001",
            "chat_id": "oc_chat_001",
            "chat_type": "p2p",
            "message_type": "text",
            "content": '{"text": "hello world"}',
            "create_time": "1623xxx",
        },
    },
}

GROUP_MESSAGE_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_002",
        "event_type": "im.message.receive_v1",
        "tenant_key": "TENANT_01",
        "app_id": "cli_abc",
        "create_time": "1623xxx",
        "token": "VTOKEN",
    },
    "event": {
        "sender": {
            "sender_id": {
                "open_id": "ou_user_002",
                "union_id": "on_xxx",
                "user_id": "usr_xxx",
            },
            "sender_type": "user",
        },
        "message": {
            "message_id": "om_msg_002",
            "chat_id": "oc_chat_002",
            "chat_type": "group",
            "message_type": "text",
            "content": '{"text": "group hello"}',
            "create_time": "1623xxx",
        },
    },
}

CARD_ACTION = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_card_001",
        "tenant_key": "TENANT_01",
        "app_id": "cli_abc",
        "token": "VTOKEN",
    },
    "event": {
        "operator": {
            "open_id": "ou_user_001",
            "union_id": "on_xxx",
            "user_id": "usr_xxx",
        },
        "token": "card_token_abc",
        "action": {
            "value": {"token": "card_token_abc"},
            "tag": "button",
            "timezone": "UTC",
        },
        "context": {
            "open_message_id": "om_card_msg_001",
            "open_chat_id": "oc_chat_001",
            "app_id": "cli_abc",
        },
    },
}


# ---------------------------------------------------------------------------
# feishu_event_to_inbound
# ---------------------------------------------------------------------------


def test_message_event_connector():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.connector == "feishu"


def test_message_event_workspace_id():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.workspace_id == "TENANT_01"


def test_message_event_external_account_id():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.external_account_id == "ou_user_001"


def test_message_event_provider_event_id():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.provider_event_id == "evt_001"


def test_message_event_chat_id():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.chat.chat_id == "oc_chat_001"


def test_message_event_chat_type_p2p_becomes_dm():
    """p2p chat_type from Feishu must map to 'dm'."""
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.chat.chat_type == "dm"


def test_message_event_chat_type_group():
    msg = feishu_event_to_inbound(GROUP_MESSAGE_EVENT)
    assert msg.chat.chat_type == "group"


def test_message_event_kind():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.kind == "message"


def test_message_event_text():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.text == "hello world"


def test_message_event_action_is_none():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.action is None


def test_message_event_chat_connector():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.chat.connector == "feishu"


def test_message_event_chat_workspace_id():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.chat.workspace_id == "TENANT_01"


def test_message_event_raw_preserved():
    msg = feishu_event_to_inbound(MESSAGE_EVENT)
    assert msg.raw == MESSAGE_EVENT


# ---------------------------------------------------------------------------
# feishu_card_action_to_inbound
# ---------------------------------------------------------------------------


def test_card_action_kind():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.kind == "card_action"


def test_card_action_connector():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.connector == "feishu"


def test_card_action_workspace_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.workspace_id == "TENANT_01"


def test_card_action_external_account_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.external_account_id == "ou_user_001"


def test_card_action_provider_event_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.provider_event_id == "evt_card_001"


def test_card_action_action_not_none():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action is not None


def test_card_action_token():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action.token == "card_token_abc"


def test_card_action_source_message_ref_connector():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action.source_message_ref.connector == "feishu"


def test_card_action_source_message_ref_workspace_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action.source_message_ref.workspace_id == "TENANT_01"


def test_card_action_source_message_ref_chat_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action.source_message_ref.chat_id == "oc_chat_001"


def test_card_action_source_message_ref_message_id():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.action.source_message_ref.message_id == "om_card_msg_001"


def test_card_action_text_is_none():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.text is None


def test_card_action_raw_preserved():
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.raw == CARD_ACTION


# ---------------------------------------------------------------------------
# outbound_card_to_feishu
# ---------------------------------------------------------------------------


def _make_card(
    *,
    title: str = "Test Title",
    body: str = "Test body text",
    sections: list[CardSection] | None = None,
    actions: list[CardAction] | None = None,
    resolved: bool = False,
    footer: str | None = None,
) -> OutboundCard:
    return OutboundCard(
        title=title,
        body=body,
        sections=sections or [],
        actions=actions or [],
        resolved=resolved,
        footer=footer,
    )


def test_card_has_schema_key():
    result = outbound_card_to_feishu(_make_card())
    assert "schema" in result


def test_card_schema_value():
    result = outbound_card_to_feishu(_make_card())
    assert result["schema"] == "2.0"


def test_card_has_config_key():
    result = outbound_card_to_feishu(_make_card())
    assert "config" in result


def test_card_wide_screen_mode():
    result = outbound_card_to_feishu(_make_card())
    assert result["config"].get("wide_screen_mode") is True


def test_card_has_header():
    result = outbound_card_to_feishu(_make_card(title="My Card"))
    assert "header" in result


def test_card_header_title_content():
    result = outbound_card_to_feishu(_make_card(title="My Card"))
    assert result["header"]["title"]["content"] == "My Card"


def test_card_header_title_tag():
    result = outbound_card_to_feishu(_make_card(title="My Card"))
    assert result["header"]["title"]["tag"] == "plain_text"


def test_card_has_body():
    result = outbound_card_to_feishu(_make_card(body="some body text"))
    assert "body" in result


def test_card_body_contains_body_text():
    result = outbound_card_to_feishu(_make_card(body="some body text"))
    elements = result["body"]["elements"]
    assert any("some body text" in str(e) for e in elements)


def _buttons(result: dict) -> list[dict]:
    """Extract schema-2.0 button elements from body.elements."""
    return [
        e
        for e in result["body"]["elements"]
        if e.get("tag") == "button"
    ]


def test_card_actions_present():
    card = _make_card(
        actions=[
            CardAction(label="Approve", style="primary", token="tok_approve"),
            CardAction(label="Reject", style="danger", token="tok_reject"),
        ]
    )
    result = outbound_card_to_feishu(card)
    # Schema 2.0: buttons live inside body.elements, NOT a top-level "actions".
    assert "actions" not in result
    assert len(_buttons(result)) == 2


def test_card_action_count():
    card = _make_card(
        actions=[
            CardAction(label="Approve", style="primary", token="tok_approve"),
            CardAction(label="Reject", style="danger", token="tok_reject"),
        ]
    )
    result = outbound_card_to_feishu(card)
    assert len(_buttons(result)) == 2


def test_card_action_label():
    card = _make_card(
        actions=[CardAction(label="Approve", style="primary", token="tok_approve")]
    )
    result = outbound_card_to_feishu(card)
    btn = _buttons(result)[0]
    assert btn["text"]["content"] == "Approve"


def test_card_action_value_only_token():
    """Each button's callback behavior value dict must contain only the token."""
    card = _make_card(
        actions=[CardAction(label="Go", style="default", token="tok_go")]
    )
    result = outbound_card_to_feishu(card)
    btn = _buttons(result)[0]
    callback = btn["behaviors"][0]
    assert callback["type"] == "callback"
    assert set(callback["value"].keys()) == {"token"}
    assert callback["value"]["token"] == "tok_go"


def test_card_action_style():
    card = _make_card(
        actions=[CardAction(label="Do it", style="danger", token="tok_danger")]
    )
    result = outbound_card_to_feishu(card)
    btn = _buttons(result)[0]
    assert btn["type"] == "danger"


def test_card_no_actions():
    card = _make_card(actions=[])
    result = outbound_card_to_feishu(card)
    # No buttons in body, and no stray top-level "actions" property.
    assert "actions" not in result
    assert _buttons(result) == []


def test_card_with_section():
    card = _make_card(
        sections=[CardSection(title="Section 1", body="section body text")]
    )
    result = outbound_card_to_feishu(card)
    body_str = json.dumps(result["body"])
    assert "section body text" in body_str


# ---------------------------------------------------------------------------
# Reply-option buttons (pickable replies rendered as a card)
# ---------------------------------------------------------------------------


def test_reply_button_value_carries_reply_and_label():
    """A reply-option button's callback value carries the reply text + label,
    NOT a token (so the pick can be replayed as a message and its card locked)."""
    card = _make_card(
        actions=[CardAction(label="Price it", style="default", reply="price the option")]
    )
    result = outbound_card_to_feishu(card)
    btn = next(e for e in result["body"]["elements"] if e.get("tag") == "button")
    value = btn["behaviors"][0]["value"]
    assert value == {"reply": "price the option", "label": "Price it"}
    assert "token" not in value


_REPLY_PICK_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_reply_001",
        "tenant_key": "TENANT_01",
        "app_id": "cli_abc",
        "token": "VTOKEN",
    },
    "event": {
        "operator": {"open_id": "ou_user_001"},
        "action": {
            "value": {"reply": "price the option", "label": "Price it"},
            "tag": "button",
        },
        "context": {
            "open_message_id": "om_reply_src_001",
            "open_chat_id": "oc_chat_001",
            "app_id": "cli_abc",
        },
    },
}


def test_reply_pick_becomes_message_inbound():
    """A reply-option pick translates to a kind='message' inbound whose text is
    the option value, carrying the source card ref + label for locking."""
    msg = feishu_card_action_to_inbound(_REPLY_PICK_EVENT)
    assert msg.kind == "message"
    assert msg.text == "price the option"
    assert msg.action is None
    assert msg.card_lock_ref is not None
    assert msg.card_lock_ref.message_id == "om_reply_src_001"
    assert msg.card_lock_label == "Price it"


def test_token_pick_still_card_action():
    """An approval (token) pick remains a card_action inbound (regression)."""
    msg = feishu_card_action_to_inbound(CARD_ACTION)
    assert msg.kind == "card_action"
    assert msg.action is not None
    assert msg.action.token == "card_token_abc"
    assert msg.card_lock_ref is None
