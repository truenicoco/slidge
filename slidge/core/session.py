import logging
from typing import TYPE_CHECKING, Any, Dict, Generic, Literal, Optional, Type

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import XMPPError

from ..core.contact import LegacyContactType, LegacyRoster, LegacyRosterType
from ..util import ABCSubclassableOnceAtMost, BiDict
from ..util.db import GatewayUser, user_store
from ..util.types import LegacyMessageType

if TYPE_CHECKING:
    from slidge import SearchResult
    from slidge.core.gateway import BaseGateway


class BaseSession(
    Generic[LegacyContactType, LegacyRosterType], metaclass=ABCSubclassableOnceAtMost
):
    """
    Represents a gateway user logged in to the network and performing actions.

    Will be instantiated automatically when a user sends an online presence to the gateway
    component, as per :xep:`0100`.

    Must be subclassed for a functional slidge plugin.
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

        self.log = logging.getLogger(user.bare_jid)

        self.user = user
        if self.store_sent:
            self.sent: BiDict = BiDict()  # TODO: set a max size for this
        self.logged = False

        self.contacts: LegacyRosterType = self._roster_cls(self)
        self.post_init()

    @staticmethod
    def legacy_msg_id_to_xmpp_msg_id(legacy_msg_id: Any) -> str:
        """
        Convert a legacy msg ID to a valid XMPP msg ID.
        Needed for read marks and message corrections.

        The default implementation just converts the legacy ID to a :class:`str`,
        but this should be overridden in case some characters needs to be escaped,
        or to add some additional, legacy network-specific logic.

        :param legacy_msg_id:
        :return: Should return a string that is usable as an XMPP stanza ID
        """
        return str(legacy_msg_id)

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> LegacyMessageType:
        """
        Convert a legacy XMPP ID to a valid XMPP msg ID.
        Needed for read marks and message corrections.

        The default implementation just converts the legacy ID to a :class:`str`,
        but this should be overridden in case some characters needs to be escaped,
        or to add some additional, legacy network-specific logic.

        The default implementation is an identity function

        :param i: The XMPP stanza ID
        :return: An ID that can be used to identify a message on the legacy network
        """
        return i

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
        Get a user's :class:`.LegacySession` using the "from" field of a stanza

        Meant to be called from :class:`BaseGateway` only.

        :param s:
        :return:
        """
        return cls._from_user_or_none(user_store.get_by_stanza(s))

    @classmethod
    def from_jid(cls, jid: JID) -> "BaseSession":
        """
        Get a user's :class:`.LegacySession` using its jid

        Meant to be called from :class:`BaseGateway` only.

        :param jid:
        :return:
        """
        return cls._from_user_or_none(user_store.get_by_jid(jid))

    @classmethod
    async def kill_by_jid(cls, jid: JID):
        """
        Terminate a user session.

        Meant to be called from :class:`BaseGateway` only.

        :param jid:
        :return:
        """
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

    async def send_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
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
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.active(self.contacts.by_stanza(m))

    async def inactive_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.inactive(self.contacts.by_stanza(m))

    async def composing_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.composing(self.contacts.by_stanza(m))

    async def paused_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        if m.get_to() != self.xmpp.boundjid.bare:
            await self.paused(self.contacts.by_stanza(m))

    async def displayed_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
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
        """
        Send a message from the gateway component to the user.

        Can be used to indicate the user session status, ie "SMS code required", "connected", …

        :param text: A text
        """
        self.xmpp.send_message(mto=self.user.jid, mbody=text, **msg_kwargs)

    async def input(self, text: str, **msg_kwargs):
        """
        Request user input via direct messages.

        Wraps call to :meth:`.BaseSession.input`

        :param text: The prompt to send to the user
        :param msg_kwargs: Extra attributes
        :return:
        """
        return await self.xmpp.input(self.user, text, **msg_kwargs)

    async def send_qr(self, text: str):
        await self.xmpp.send_qr(text, mto=self.user.jid)

    def post_init(self):
        """
        Add useful attributes for your session here, if you wish.

        In most cases, this is the right place to add a legacy network-specific
        ``LegacyClient``-like instance attached to this gateway user.
        """
        pass

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

        Triggered when the gateway receives an offline presence from the user.
        Just override this and ``pass`` to implement a bouncer-like ("always connected") functionality.

        :param p:
        """
        raise NotImplementedError

    async def send_text(
        self, t: str, c: LegacyContactType
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user sends a text message from xmpp to a bridged contact, e.g.
        to ``translated_user_name@slidge.example.com``.

        Override this and implement sending a message to the legacy network in this method.

        :param t: Content of the message
        :param c: Recipient of the message
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def send_file(
        self, u: str, c: LegacyContactType
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user has sends a file using HTTP Upload (:xep:`0363`)

        :param u: URL of the file
        :param c: Recipient of the file
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: LegacyContactType):
        """
        Triggered when the user sends an 'active' chat state to the legacy network (:xep:`0085`)

        :param c: Recipient of the active chat state
        """
        raise NotImplementedError

    async def inactive(self, c: LegacyContactType):
        """
        Triggered when the user sends an 'inactive' chat state to the legacy network (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def composing(self, c: LegacyContactType):
        """
        Triggered when the user starts typing in the window of a legacy contact (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def paused(self, c: LegacyContactType):
        """
        Triggered when the user pauses typing in the window of a legacy contact (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def displayed(self, legacy_msg_id: Any, c: LegacyContactType):
        """
        Triggered when the user reads a message sent by a legacy contact.  (:xep:`0333`)

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.sent_text` or :meth:`slidge.LegacyContact.send_file`.

        :param legacy_msg_id: Identifier of the message, passed to :meth:`slidge.LegacyContact.send_text`
            or :meth:`slidge.LegacyContact.send_file`
        :param c:
        """
        raise NotImplementedError

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        """
        Triggered when the user corrected a message using :xep:`0308`

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.sent_text` or :meth:`slidge.LegacyContact.send_file`.

        :param text:
        :param legacy_msg_id:
        :param c:
        """
        raise NotImplementedError

    async def search(self, form_values: Dict[str, str]) -> "SearchResult":
        """
        Triggered when the user uses Jabber Search (:xep:`0055`) on the component

        Form values is a dict in which keys are defined in :attr:`.BaseGateway.SEARCH_FIELDS`

        :param form_values: search query, defined for a specific plugin by overriding
            in :attr:`.BaseGateway.SEARCH_FIELDS`
        :return:
        """
        raise NotImplementedError


_sessions: Dict[GatewayUser, BaseSession] = {}
log = logging.getLogger(__name__)
