from abc import ABCMeta
from typing import TYPE_CHECKING, Union

from slixmpp import JID, Message, Presence

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
