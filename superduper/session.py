"""
User actions
"""

from typing import TYPE_CHECKING, Iterable, Optional, Union

from slidge import BaseSession, GatewayUser
from slidge.util.types import LinkPreview, Mention

from .group import MUC, Participant
from .legacy_client import SuperDuperClient

if TYPE_CHECKING:
    from .contact import Contact

Recipient = Union["Contact", MUC]
Sender = Union["Contact", Participant]


class Session(BaseSession[str, Recipient]):
    def __init__(self, user: GatewayUser):
        super().__init__(user)
        self.legacy_client = SuperDuperClient(self)
        self.contacts.user_legacy_id = self.legacy_client.user_id

    async def login(self):
        await self.legacy_client.login()
        return "Success!"

    async def on_text(
        self,
        chat: Recipient,
        text: str,
        *,
        reply_to_msg_id: Optional[str] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional[Sender] = None,  # type:ignore
        thread: Optional[str] = None,  # type:ignore
        link_previews: Iterable[LinkPreview] = (),
        mentions: Optional[list[Mention]] = None,
    ) -> Optional[str]:
        if chat.is_group:
            assert isinstance(chat, MUC)
            msg = await self.legacy_client.send_group_msg(text, chat.legacy_id)
        else:
            msg = await self.legacy_client.send_direct_msg(text, chat.legacy_id)
        return msg.id
