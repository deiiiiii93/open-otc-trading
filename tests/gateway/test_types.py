from app.services.gateway.types import InboundMessage, ChatRef, OutboundCard, CardAction


def test_inbound_message_card_action_has_no_text():
    chat = ChatRef("feishu", "tk_1", "oc_1", "dm")
    msg = InboundMessage("feishu", "tk_1", "ou_1", "evt_1", chat, "card_action", None, None, {})
    assert msg.kind == "card_action" and msg.text is None


def test_card_action_carries_only_token_to_button():
    a = CardAction(label="Approve", style="primary", token="tok_abc")
    assert a.token == "tok_abc" and not hasattr(a, "action_id")
