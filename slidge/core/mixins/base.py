from abc import ABCMeta
from typing import TYPE_CHECKING

from slixmpp import JID

from ...util.types import MessageOrPresenceTypeVar

if TYPE_CHECKING:
    from ..gateway import BaseGateway
    from ..session import BaseSession


class MetaBase(ABCMeta):
    pass


class Base:
    session: "BaseSession" = NotImplemented
    xmpp: "BaseGateway" = NotImplemented

    jid: JID = NotImplemented
    name: str = NotImplemented

    @property
    def user_jid(self):
        return self.session.user_jid

    @property
    def user_pk(self):
        return self.session.user_pk


class BaseSender(Base):
    def _send(
        self, stanza: MessageOrPresenceTypeVar, **send_kwargs
    ) -> MessageOrPresenceTypeVar:
        raise NotImplementedError
