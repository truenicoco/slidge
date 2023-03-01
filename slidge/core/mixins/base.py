from abc import ABCMeta
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import JID, Message, Presence
from slixmpp.plugins.xep_0004.stanza import Form

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
    xmpp: "BaseGateway" = NotImplemented

    async def restricted_emoji_extended_feature(self):
        available = await self.available_emojis()
        if not self.REACTIONS_SINGLE_EMOJI and available is None:
            return None

        form = Form()
        form["type"] = "result"
        form.add_field("FORM_TYPE", "hidden", value="urn:xmpp:reactions:0:restrictions")
        if self.REACTIONS_SINGLE_EMOJI:
            form.add_field("max_reactions_per_user", value="1")
        if available:
            form.add_field("allowlist", value=list(available))
        return form

    async def available_emojis(
        self, legacy_msg_id: Optional[LegacyMessageType] = None
    ) -> Optional[set[str]]:
        """
        Override this to restrict the subset of reactions this recipient
        can handle.

        :return: A set of emojis or None if any emoji is allowed
        """
        return None


class ThreadRecipientMixin:
    async def create_thread(self, xmpp_id: str) -> Union[int, str]:
        return xmpp_id
