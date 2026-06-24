from dataclasses import dataclass
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class ChatRef:
    connector: str
    workspace_id: str
    chat_id: str
    chat_type: Literal["dm", "group"]


@dataclass(frozen=True)
class MessageRef:
    connector: str
    workspace_id: str
    chat_id: str
    message_id: str


@dataclass(frozen=True)
class OutboundMessage:
    text: str


@dataclass(frozen=True)
class CardAction:
    label: str
    style: Literal["primary", "danger", "default"]
    token: str


@dataclass(frozen=True)
class CardSection:
    title: str
    body: str


@dataclass(frozen=True)
class OutboundCard:
    title: str
    body: str
    sections: list["CardSection"]
    actions: list["CardAction"]
    resolved: bool
    footer: Optional[str]


@dataclass(frozen=True)
class CardActionInbound:
    source_message_ref: MessageRef
    token: str


@dataclass(frozen=True)
class InboundMessage:
    connector: str
    workspace_id: str
    external_account_id: str
    provider_event_id: str
    chat: ChatRef
    kind: Literal["message", "card_action"]
    text: Optional[str]
    action: Optional[CardActionInbound]
    raw: Any


@dataclass(frozen=True)
class ConnectorCapabilities:
    supports_edit_in_place_message: bool
    supports_edit_in_place_card: bool
    supports_interactive_cards: bool
    max_message_chars: int


@dataclass(frozen=True)
class ConnectorHealth:
    name: str
    state: Literal["healthy", "degraded", "unhealthy"]
    detail: str


@dataclass(frozen=True)
class AgentEvent:
    type: str
    data: Any
