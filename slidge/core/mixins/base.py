from abc import ABCMeta
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import JID, Message, Presence

from slidge.util.types import LegacyMessageType

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway
    from slidge.core.session import BaseSession
    from slidge.util.db import GatewayUser


class MetaBase(ABCMeta):
    pass


class Base:
    session: "BaseSession" = NotImplemented
    xmpp: "BaseGateway" = NotImplemented
    user: "GatewayUser" = NotImplemented

    jid: JID = NotImplemented
    name: str = NotImplemented


class BaseSender(Base):
    def _send(self, stanza: Union[Message, Presence], **send_kwargs):
        raise NotImplementedError


class ReactionRecipientMixin:
    REACTIONS_SINGLE_EMOJI = False

    async def available_emojis(
        self, legacy_msg_id: LegacyMessageType
    ) -> Optional[set[str]]:
        """
        Override this to restrict the subset of reactions this recipient
        can handle.

        :return: A set of emojis or None if any emoji is allowed
        """
        return None
