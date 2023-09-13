import asyncio
import logging
from typing import TYPE_CHECKING, Any, Generic, NamedTuple, Optional, Union, cast

import aiohttp
from slixmpp import JID, Message
from slixmpp.exceptions import XMPPError
from slixmpp.types import PresenceShows

from ..util import ABCSubclassableOnceAtMost
from ..util.db import GatewayUser, user_store
from ..util.sql import SQLBiDict
from ..util.types import (
    LegacyMessageType,
    LegacyThreadType,
    PseudoPresenceShow,
    RecipientType,
    ResourceDict,
)
from .command.base import SearchResult
from .contact import LegacyContact, LegacyRoster
from .muc.bookmarks import LegacyBookmarks
from .muc.room import LegacyMUC

if TYPE_CHECKING:
    from ..util.types import Sender
    from .gateway import BaseGateway
    from .muc.participant import LegacyParticipant


class CachedPresence(NamedTuple):
    status: Optional[str]
    show: Optional[str]
    kwargs: dict[str, Any]


class BaseSession(
    Generic[LegacyMessageType, RecipientType], metaclass=ABCSubclassableOnceAtMost
):
    """
    Represents a gateway user logged in to the legacy network and performing actions.

    Will be instantiated automatically when a user sends an online presence to the gateway
    component, as per :xep:`0100`.

    Must be subclassed for a functional slidge plugin.
    """

    """
    Since we cannot set the XMPP ID of messages sent by XMPP clients, we need to keep a mapping
    between XMPP IDs and legacy message IDs if we want to further refer to a message that was sent
    by the user. This also applies to 'carboned' messages, ie, messages sent by the user from
    the official client of a legacy network.
    """

    xmpp: "BaseGateway"
    """
    The gateway instance singleton. Use it for low-level XMPP calls or custom methods that are not
    session-specific.
    """

    http: aiohttp.ClientSession

    MESSAGE_IDS_ARE_THREAD_IDS = False
    """
    Set this to True if the legacy service uses message IDs as thread IDs,
    eg Mattermost, where you can only 'create a thread' by replying to the message,
    in which case the message ID is also a thread ID (and all messages are potential
    threads).
    """

    def __init__(self, user: GatewayUser):
        self.log = logging.getLogger(user.bare_jid)

        self.user = user
        self.sent = SQLBiDict[LegacyMessageType, str](
            "session_message_sent", "legacy_id", "xmpp_id", self.user
        )
        # message ids (*not* stanza-ids), needed for last msg correction
        self.muc_sent_msg_ids = SQLBiDict[LegacyMessageType, str](
            "session_message_sent_muc", "legacy_id", "xmpp_id", self.user
        )

        self.ignore_messages = set[str]()

        self.contacts: LegacyRoster = LegacyRoster.get_self_or_unique_subclass()(self)
        self._logged = False
        self.__reset_ready()

        self.bookmarks: LegacyBookmarks = LegacyBookmarks.get_self_or_unique_subclass()(
            self
        )

        self.http = self.xmpp.http

        self.threads = SQLBiDict[str, LegacyThreadType](  # type:ignore
            "session_thread_sent_muc", "legacy_id", "xmpp_id", self.user
        )
        self.thread_creation_lock = asyncio.Lock()

        self.__cached_presence: Optional[CachedPresence] = None

    def __reset_ready(self):
        self.ready = self.xmpp.loop.create_future()

    @property
    def logged(self):
        return self._logged

    @logged.setter
    def logged(self, v: bool):
        self._logged = v
        if self.ready.done():
            if v:
                return
            self.__reset_ready()
        else:
            if v:
                self.ready.set_result(True)

    def __repr__(self):
        return f"<Session of {self.user}>"

    def shutdown(self):
        for c in self.contacts:
            c.offline()
        for m in self.bookmarks:
            m.shutdown()
        self.xmpp.loop.create_task(self.logout())

    @staticmethod
    def legacy_msg_id_to_xmpp_msg_id(legacy_msg_id: LegacyMessageType) -> str:
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
        return cast(LegacyMessageType, i)

    def raise_if_not_logged(self):
        if not self.logged:
            raise XMPPError(
                "internal-server-error",
                text="You are not logged to the legacy network",
            )

    @classmethod
    def _from_user_or_none(cls, user):
        if user is None:
            log.debug("user not found", stack_info=True)
            raise XMPPError(text="User not found", condition="subscription-required")

        session = _sessions.get(user)
        if session is None:
            _sessions[user] = session = cls(user)
        return session

    @classmethod
    def from_user(cls, user):
        return cls._from_user_or_none(user)

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
        await cls.xmpp.unregister(user)
        del _sessions[user]
        del user
        del session

    def __ack(self, msg: Message):
        if not self.xmpp.PROPER_RECEIPTS:
            self.xmpp.delivery_receipt.ack(msg)

    def send_gateway_status(
        self,
        status: Optional[str] = None,
        show=Optional[PresenceShows],
        **kwargs,
    ):
        """
        Send a presence from the gateway to the user.

        Can be used to indicate the user session status, ie "SMS code required", "connected", …

        :param status: A status message
        :param show: Presence stanza 'show' element. I suggest using "dnd" to show
            that the gateway is not fully functional
        """
        self.__cached_presence = CachedPresence(status, show, kwargs)
        self.xmpp.send_presence(
            pto=self.user.bare_jid, pstatus=status, pshow=show, **kwargs
        )

    def send_cached_presence(self, to: JID):
        if not self.__cached_presence:
            self.xmpp.send_presence(pto=to, ptype="unavailable")
            return
        self.xmpp.send_presence(
            pto=to,
            pstatus=self.__cached_presence.status,
            pshow=self.__cached_presence.show,
            **self.__cached_presence.kwargs,
        )

    def send_gateway_message(self, text: str, **msg_kwargs):
        """
        Send a message from the gateway component to the user.

        Can be used to indicate the user session status, ie "SMS code required", "connected", …

        :param text: A text
        """
        self.xmpp.send_text(text, mto=self.user.jid, **msg_kwargs)

    def send_gateway_invite(
        self,
        muc: LegacyMUC,
        reason: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Send an invitation to join a MUC, emanating from the gateway component.

        :param muc:
        :param reason:
        :param password:
        """
        self.xmpp.invite_to(muc, reason=reason, password=password, mto=self.user.jid)

    async def input(self, text: str, **msg_kwargs):
        """
        Request user input via direct messages.

        Wraps call to :meth:`.BaseSession.input`

        :param text: The prompt to send to the user
        :param msg_kwargs: Extra attributes
        :return:
        """
        return await self.xmpp.input(self.user.jid, text, **msg_kwargs)

    async def send_qr(self, text: str):
        """
        Sends a QR code generated from 'text' via HTTP Upload and send the URL to
        ``self.user``

        :param text: Text to encode as a QR code
        """
        await self.xmpp.send_qr(text, mto=self.user.jid)

    async def login(self) -> Optional[str]:
        """
        Login the gateway user to the legacy network.

        Triggered when the gateway start and on user registration.
        It is recommended that this function returns once the user is logged in,
        so if you need to await forever (for instance to listen to incoming events),
        it's a good idea to wrap your listener in an asyncio.Task.

        :return: Optionally, a text to use as the gateway status, e.g., "Connected as 'dude@legacy.network'"
        """
        raise NotImplementedError

    async def logout(self):
        """
        Logout the gateway user from the legacy network.

        Called on user unregistration and gateway shutdown.
        """
        raise NotImplementedError

    def re_login(self):
        """
        Logout then re-login

        No reason to override this
        """
        self.xmpp.re_login(self)

    async def send_text(
        self,
        chat: RecipientType,
        text: str,
        *,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional["Sender"] = None,
        thread: Optional[LegacyThreadType] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user sends a text message from XMPP to a bridged entity, e.g.
        to ``translated_user_name@slidge.example.com``, or ``translated_group_name@slidge.example.com``

        Override this and implement sending a message to the legacy network in this method.

        :param text: Content of the message
        :param chat: RecipientType of the message. :class:`.LegacyContact` instance for 1:1 chat,
            :class:`.MUC` instance for groups.
        :param reply_to_msg_id: A legacy message ID if the message references (quotes)
            another message (:xep:`0461`)
        :param reply_to_fallback_text: Content of the quoted text. Not necessarily set
            by XMPP clients
        :param reply_to: Author of the quoted message. :class:`LegacyContact` instance for
            1:1 chat, :class:`LegacyParticipant` instance for groups.
            If `None`, should be interpreted as a self-reply if reply_to_msg_id is not None.
        :param thread:

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def send_file(
        self,
        chat: RecipientType,
        url: str,
        *,
        http_response: aiohttp.ClientResponse,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional[Union["LegacyContact", "LegacyParticipant"]] = None,
        thread: Optional[LegacyThreadType] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user has sends a file using HTTP Upload (:xep:`0363`)

        :param url: URL of the file
        :param chat: See :meth:`.BaseSession.send_text`
        :param http_response: The HTTP GET response object on the URL
        :param reply_to_msg_id: See :meth:`.BaseSession.send_text`
        :param reply_to_fallback_text: See :meth:`.BaseSession.send_text`
        :param reply_to: See :meth:`.BaseSession.send_text`
        :param thread:

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: RecipientType, thread: Optional[LegacyThreadType] = None):
        """
        Triggered when the user sends an 'active' chat state to the legacy network (:xep:`0085`)

        :param thread:
        :param c: RecipientType of the active chat state
        """
        raise NotImplementedError

    async def inactive(
        self, c: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user sends an 'inactive' chat state to the legacy network (:xep:`0085`)

        :param thread:
        :param c:
        """
        raise NotImplementedError

    async def composing(
        self, c: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user starts typing in the window of a legacy contact (:xep:`0085`)

        :param thread:
        :param c:
        """
        raise NotImplementedError

    async def paused(self, c: RecipientType, thread: Optional[LegacyThreadType] = None):
        """
        Triggered when the user pauses typing in the window of a legacy contact (:xep:`0085`)

        :param thread:
        :param c:
        """
        raise NotImplementedError

    async def displayed(
        self,
        c: RecipientType,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user reads a message sent by a legacy contact.  (:xep:`0333`)

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.sent_text` or :meth:`slidge.LegacyContact.send_file`.

        :param thread:
        :param legacy_msg_id: Identifier of the message, passed to :meth:`slidge.LegacyContact.send_text`
            or :meth:`slidge.LegacyContact.send_file`
        :param c:
        """
        raise NotImplementedError

    async def correct(
        self,
        c: RecipientType,
        text: str,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user corrected a message using :xep:`0308`

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.send_text` or :meth:`slidge.LegacyContact.send_file`.

        :param thread:
        :param text:
        :param legacy_msg_id:
        :param c:
        """
        raise NotImplementedError

    async def search(self, form_values: dict[str, str]) -> Optional[SearchResult]:
        """
        Triggered when the user uses Jabber Search (:xep:`0055`) on the component

        Form values is a dict in which keys are defined in :attr:`.BaseGateway.SEARCH_FIELDS`

        :param form_values: search query, defined for a specific plugin by overriding
            in :attr:`.BaseGateway.SEARCH_FIELDS`
        :return:
        """
        raise NotImplementedError

    async def react(
        self,
        c: RecipientType,
        legacy_msg_id: LegacyMessageType,
        emojis: list[str],
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user sends message reactions (:xep:`0444`).

        :param thread:
        :param legacy_msg_id: ID of the message the user reacts to
        :param emojis: Unicode characters representing reactions to the message ``legacy_msg_id``.
            An empty string means "no reaction", ie, remove all reactions if any were present before
        :param c: Contact or MUC the reaction refers to
        """
        raise NotImplementedError

    async def retract(
        self,
        c: RecipientType,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user retracts (:xep:`0424`) a message.

        :param thread:
        :param legacy_msg_id: Legacy ID of the retracted message
        :param c: The contact this retraction refers to
        """
        raise NotImplementedError

    async def get_contact_or_group_or_participant(self, jid: JID):
        if jid.bare in (contacts := self.contacts.known_contacts(only_friends=False)):
            return contacts[jid.bare]
        if jid.bare in (mucs := self.bookmarks._mucs_by_bare_jid):
            return await self.__get_muc_or_participant(mucs[jid.bare], jid)
        else:
            muc = None

        try:
            return await self.contacts.by_jid(jid)
        except XMPPError:
            if muc is None:
                try:
                    muc = await self.bookmarks.by_jid(jid)
                except XMPPError:
                    return
            return await self.__get_muc_or_participant(muc, jid)

    @staticmethod
    async def __get_muc_or_participant(muc: LegacyMUC, jid: JID):
        if nick := jid.resource:
            try:
                return await muc.get_participant(
                    nick, raise_if_not_found=True, fill_first=True
                )
            except XMPPError:
                return None
        return muc

    async def wait_for_ready(self, timeout: Optional[Union[int, float]] = 10):
        """
        Wait until session, contacts and bookmarks are ready

        (slidge internal use)

        :param timeout:
        :return:
        """
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), timeout)
            await asyncio.wait_for(asyncio.shield(self.contacts.ready), timeout)
            await asyncio.wait_for(asyncio.shield(self.bookmarks.ready), timeout)
        except asyncio.TimeoutError:
            raise XMPPError(
                "recipient-unavailable",
                "Legacy session is not fully initialized, retry later",
            )

    async def presence(
        self,
        resource: str,
        show: PseudoPresenceShow,
        status: str,
        resources: dict[str, ResourceDict],
        merged_resource: Optional[ResourceDict],
    ):
        """
        Called when the gateway component receives a presence, ie, when
        one of the user's clients goes online of offline, or changes its
        status.

        :param resource: The XMPP client identifier, arbitrary string.
        :param show: The presence ``<show>``, if available. If the resource is
            just 'available' without any ``<show>`` element, this is an empty
            str.
        :param status: A status message, like a deeply profound quote, eg,
            "Roses are red, violets are blue, [INSERT JOKE]".
        :param resources: A summary of all the resources for this user.
        :param merged_resource: A global presence for the user account,
            following rules described in :meth:`merge_resources`
        """
        raise NotImplementedError


_sessions: dict[GatewayUser, BaseSession] = {}
log = logging.getLogger(__name__)
