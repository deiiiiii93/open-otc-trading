import pytest
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import ChatRef, OutboundMessage


@pytest.mark.asyncio
async def test_idempotent_send_does_not_duplicate():
    c = FakeConnector()
    chat = ChatRef("fake", "", "chat1", "dm")
    r1 = await c.send_message(chat, OutboundMessage("hi"), idempotency_key="k1")
    r2 = await c.send_message(chat, OutboundMessage("hi"), idempotency_key="k1")
    assert r1 == r2
    assert len(c.outbox) == 1
