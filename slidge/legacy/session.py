import logging
from typing import Type, Dict, Any, Optional, Hashable, TYPE_CHECKING, Generic, Literal

from slixmpp import Message, Presence, JID
from slixmpp.exceptions import XMPPError

from ..db import GatewayUser, user_store
from ..util import BiDict, ABCSubclassableOnceAtMost
from .contact import LegacyContactType, LegacyRosterType, LegacyRoster

if TYPE_CHECKING:
    from ..gateway import BaseGateway


class BaseSession(
    Generic[LegacyContactType, LegacyRosterType], metaclass=ABCSubclassableOnceAtMost
):
    """
    Represents a gateway user logged in to the network and performing actions.

    Must be overridden for a functional slidge plugin
    """

    store_sent = True
    """
    Keep track of sent messages. Useful to later update the messages' status, e.g.,
    with a read mark from the recipient
    """

    xmpp: "BaseGateway"

    def __init__(self, user: GatewayUser):
        self._roster_cls: Type[
            LegacyRosterType
        ] = LegacyRoster.get_self_or_unique_subclass()

        self.user = user
        if self.store_sent:
            self.sent: BiDict = BiDict()  # TODO: set a max size for this
        self.logged = False

        self.contacts: LegacyRosterType = self._roster_cls(self)
        self.post_init()

    @staticmethod
    def legacy_msg_id_to_xmpp_msg_id(legacy_msg_id: Any):
        return str(legacy_msg_id)

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> Any:
        return i

    def post_init(self):
        """
        Add useful attributes for your session here, if necessary
        """
        pass

    @classmethod
    def _from_user_or_none(cls, user):
        if user is None:
            raise XMPPError(
                text="User not found", condition="subscription-required", etype="auth"
            )

        session = _sessions.get(user)
        if session is None:
            _sessions[user] = session = cls(user)
        return session

    @classmethod
    def from_stanza(cls, s) -> "BaseSession":
        """
        Get a user's :class:`LegacySession` using the "from" field of a stanza

        Ensure that we only have a single session instance per user

        :param s:
        :return:
        """
        return cls._from_user_or_none(user_store.get_by_stanza(s))

    @classmethod
    def from_jid(cls, jid: JID):
        return cls._from_user_or_none(user_store.get_by_jid(jid))

    @classmethod
    async def kill_by_jid(cls, jid: JID):
        log.debug("Killing session of %s", jid)
        for user, session in _sessions.items():
            if user.jid == jid.bare:
                break
        else:
            log.debug("Did not find a session for %s", jid)
            return
        for c in session.contacts:
            c.unsubscribe()
        await session.logout(None)
        await cls.xmpp.unregister(user)
        del _sessions[user]
        del user
        del session

    async def login(self, p: Presence):
        """
        Login the gateway user to the legacy network.

        Triggered when the gateway receives an online presence from the user, so the legacy client
        should keep a list of logged-in users to avoid useless calls to the login process.

        :param p:
        """
        raise NotImplementedError

    async def logout(self, p: Optional[Presence]):
        """
        Logout the gateway user from the legacy network.

        Called when the gateway receives an offline presence from the user.
        Just override this and ``pass`` to implement a bouncer-like ("always connected") functionality.

        :param p:
        """
        raise NotImplementedError

    async def send_from_msg(self, m: Message):
        if m["replace"][
            "id"
        ]:  # ignore last message correction (handled by a specific method)
            return
        url = m["oob"]["url"]
        text = m["body"]
        if url:
            legacy_msg_id = await self.send_file(url, self.contacts.by_stanza(m))
        elif text:
            legacy_msg_id = await self.send_text(text, self.contacts.by_stanza(m))
        else:
            log.debug("Ignoring %s", m)
            return
        self.sent[legacy_msg_id] = m.get_id()

    async def active_from_msg(self, m: Message):
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.active(self.contacts.by_stanza(m))

    async def inactive_from_msg(self, m: Message):
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.inactive(self.contacts.by_stanza(m))

    async def composing_from_msg(self, m: Message):
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.composing(self.contacts.by_stanza(m))

    async def paused_from_msg(self, m: Message):
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.paused(self.contacts.by_stanza(m))

    async def displayed_from_msg(self, m: Message):
        displayed_msg_id = m["displayed"]["id"]
        try:
            legacy_msg_id = self.xmpp_msg_id_to_legacy_msg_id(displayed_msg_id)
        except NotImplementedError:
            log.debug("Couldn't convert xmpp msg ID to legacy ID, ignoring read mark")
            return

        await self.displayed(legacy_msg_id, self.contacts.by_stanza(m))

    async def correct_from_msg(self, m: Message):
        xmpp_id = m["replace"]["id"]
        legacy_id = self.sent.inverse.get(xmpp_id)
        if legacy_id is None:
            log.debug("Did not find legacy ID to correct")
            await self.send_text(m["body"], self.contacts.by_stanza(m))
        else:
            await self.correct(m["body"], legacy_id, self.contacts.by_stanza(m))

    async def send_text(self, t: str, c: LegacyContactType) -> Optional[Hashable]:
        """
        The user wants to send a text message from xmpp to the legacy network

        :param t: Content of the message
        :param c: Recipient of the message
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def send_file(self, u: str, c: LegacyContactType) -> Optional[Hashable]:
        """
        The user has sent a file using HTTP Upload

        :param u: URL of the file
        :param c: Recipient of the file
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: LegacyContactType):
        """
        The use sens an 'active' chat state to the legacy network

        :param c: Recipient of the active chat state
        """
        raise NotImplementedError

    async def inactive(self, c: LegacyContactType):
        """
        The user sends an 'inactive' chat state to the legacy network

        :param c:
        """
        raise NotImplementedError

    async def composing(self, c: LegacyContactType):
        """
        The user is typing

        :param c:
        """
        raise NotImplementedError

    async def paused(self, c: LegacyContactType):
        """
        The user paused typing

        :param c:
        """
        raise NotImplementedError

    async def displayed(self, legacy_msg_id: Any, c: LegacyContactType):
        """
        The user has read a message

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user.

        :param legacy_msg_id: Identifier of the message, passed to :meth:`slidge.LegacyContact.send_text`
            or :meth:`slidge.LegacyContact.send_file`
        :param c:
        :return:
        """
        raise NotImplementedError

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        """
        The user corrected a message using :xep:`308`

        :param text:
        :param legacy_msg_id:
        :param c:
        :return:
        """
        raise NotImplementedError

    async def search(self, form_values: Dict[str, str]):
        raise NotImplementedError

    def send_gateway_status(
        self,
        status: Optional[str] = None,
        show=Optional[Literal["away", "chat", "dnd", "xa"]],
        **kwargs
    ):
        """
        Send a presence from the gateway to the user.

        Can be used to indicate the user session status, ie "SMS code required", "connected", …

        :param status: A status message
        :param show: Presence stanza 'show' element. I suggest using "dnd" to show
            that the gateway is not fully functional
        """
        self.xmpp.send_presence(
            pto=self.user.bare_jid, pstatus=status, pshow=show, **kwargs
        )

    def send_gateway_message(self, text, **msg_kwargs):
        self.xmpp.send_message(mto=self.user.jid, mbody=text, **msg_kwargs)

    async def input(self, text: str, **msg_kwargs):
        return await self.xmpp.input(self.user, text, **msg_kwargs)

    async def send_qr(self, text: str):
        await self.xmpp.send_qr(text, mto=self.user.jid)


_sessions: Dict[GatewayUser, BaseSession] = {}
log = logging.getLogger(__name__)
