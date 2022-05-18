import logging
from abc import ABC
from typing import Type, Dict, Any, Optional, Hashable

from slixmpp import Message, Presence
from slixmpp.exceptions import XMPPError

from ..gateway import BaseGateway
from ..db import GatewayUser, user_store
from .contact import LegacyContact
from .util import get_unique_subclass


class BaseSession(ABC):
    """
    Represents a gateway user logged in to the network and performing actions.

    Must be overridden for a functional slidge plugin
    """

    store_unacked = True
    """
    If the legacy network supports message receipts, keep track of messages for later
    sending back a receipt to the user.
    """
    store_unread = True
    """
    If the legacy network supports 'read marks', keep track of messages sent by the user
    to later mark them as read by their contacts
    """
    store_unread_by_user = True
    """
    If the legacy network supports 'read marks', keep track of messages received by the user
    and transmit read marks from XMPP to the legacy network
    """
    xmpp: BaseGateway

    def __init__(self, user: GatewayUser):
        from .contact import LegacyRoster  # circular import hell

        self._roster_cls: Type[LegacyRoster] = get_unique_subclass(LegacyRoster)

        self.user = user
        if self.store_unacked:
            self.unacked: Dict[Any, Message] = {}
        if self.store_unread:
            self.unread: Dict[Any, Message] = {}
        if self.store_unread_by_user:
            self.unread_by_user: Dict[str, Any] = {}
        self.logged = False

        self.contacts = self._roster_cls(self)
        self.post_init()

    def post_init(self):
        """
        Add useful attributes for your session here, if necessary
        """
        pass

    @classmethod
    def from_stanza(cls, s) -> "BaseSession":
        """
        Get a user's :class:`LegacySession` using the "from" field of a stanza

        Ensure that we only have a single session instance per user

        :param s:
        :return:
        """
        user = user_store.get_by_stanza(s)
        if user is None:
            raise XMPPError(
                text="User not found", condition="subscription-required", etype="auth"
            )

        session = sessions.get(user)
        if session is None:
            sessions[user] = session = cls(user)
        return session

    async def login(self, p: Presence):
        """
        Login the gateway user to the legacy network.

        Triggered when the gateway receives an online presence from the user, so the legacy client
        should keep a list of logged-in users to avoid useless calls to the login process.

        :param p:
        """
        raise NotImplementedError

    async def logout(self, p: Presence):
        """
        Logout the gateway user from the legacy network.

        Called when the gateway receives an offline presence from the user.
        Just override this and ``pass`` to implement a bouncer-like ("always connected") functionality.

        :param p:
        """
        raise NotImplementedError

    async def send_from_msg(self, m: Message):
        url = m["oob"]["url"]
        text = m["body"]
        if url:
            legacy_msg_id = await self.send_file(url, self.contacts.by_stanza(m))
        elif text:
            legacy_msg_id = await self.send_text(text, self.contacts.by_stanza(m))
        else:
            log.debug("Ignoring %s", m)
            return
        self.xmpp.ack(m)
        if self.store_unacked:
            self.unacked[legacy_msg_id] = m
        if self.store_unread:
            self.unread[legacy_msg_id] = m

    async def active_from_msg(self, m: Message):
        await self.active(self.contacts.by_stanza(m))

    async def inactive_from_msg(self, m: Message):
        await self.inactive(self.contacts.by_stanza(m))

    async def composing_from_msg(self, m: Message):
        await self.composing(self.contacts.by_stanza(m))

    async def displayed_from_msg(self, m: Message):
        displayed_msg_id = m["displayed"]["id"]
        try:
            legacy_msg_id = self.unread_by_user.pop(displayed_msg_id)
        except KeyError:
            log.debug(
                "Received read marker for a msg we did not send: %s",
                self.unread_by_user,
            )
        else:
            await self.displayed(legacy_msg_id, self.contacts.by_stanza(m))

    async def send_text(self, t: str, c: LegacyContact) -> Optional[Hashable]:
        """
        The user wants to send a text message from xmpp to the legacy network

        :param t: Content of the message
        :param c: Recipient of the message
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def send_file(self, u: str, c: LegacyContact) -> Optional[Hashable]:
        """
        The user has sent a file using HTTP Upload

        :param u: URL of the file
        :param c: Recipient of the file
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: LegacyContact):
        """
        The use sens an 'active' chat state to the legacy network

        :param c: Recipient of the active chat state
        """
        raise NotImplementedError

    async def inactive(self, c: LegacyContact):
        """
        The use sens an 'inactive' chat state to the legacy network

        :param c:
        :return:
        """
        raise NotImplementedError

    async def composing(self, c: LegacyContact):
        """
        The use sens an 'inactive' starts typing

        :param c:
        :return:
        """
        raise NotImplementedError

    async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
        """


        :param legacy_msg_id: Identifier of the message, return value of by :meth:`.BaseSession.send`
        :param c:
        :return:
        """
        raise NotImplementedError


sessions: Dict[GatewayUser, BaseSession] = {}
log = logging.getLogger(__name__)
