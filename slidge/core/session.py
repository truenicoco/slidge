import asyncio
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Iterable,
    NamedTuple,
    Optional,
    Union,
    cast,
)

import aiohttp
from slixmpp import JID, Message
from slixmpp.exceptions import XMPPError
from slixmpp.types import PresenceShows

from ..command import SearchResult
from ..contact import LegacyContact, LegacyRoster
from ..db.models import GatewayUser
from ..group.bookmarks import LegacyBookmarks
from ..group.room import LegacyMUC
from ..util import ABCSubclassableOnceAtMost
from ..util.types import (
    LegacyGroupIdType,
    LegacyMessageType,
    LegacyThreadType,
    LinkPreview,
    Mention,
    PseudoPresenceShow,
    RecipientType,
    ResourceDict,
    Sticker,
)
from ..util.util import deprecated

if TYPE_CHECKING:
    from ..group.participant import LegacyParticipant
    from ..util.types import Sender
    from .gateway import BaseGateway


class CachedPresence(NamedTuple):
    status: Optional[str]
    show: Optional[str]
    kwargs: dict[str, Any]


class BaseSession(
    Generic[LegacyMessageType, RecipientType], metaclass=ABCSubclassableOnceAtMost
):
    """
    The session of a registered :term:`User`.

    Represents a gateway user logged in to the legacy network and performing actions.

    Will be instantiated automatically on slidge startup for each registered user,
    or upon registration for new (validated) users.

    Must be subclassed for a functional :term:`Legacy Module`.
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

    MESSAGE_IDS_ARE_THREAD_IDS = False
    """
    Set this to True if the legacy service uses message IDs as thread IDs,
    eg Mattermost, where you can only 'create a thread' by replying to the message,
    in which case the message ID is also a thread ID (and all messages are potential
    threads).
    """
    SPECIAL_MSG_ID_PREFIX: Optional[str] = None
    """
    If you set this, XMPP message IDs starting with this won't be converted to legacy ID,
    but passed as is to :meth:`.on_react`, and usual checks for emoji restriction won't be
    applied.
    This can be used to implement voting in polls in a hacky way.
    """

    def __init__(self, user: GatewayUser):
        self.log = logging.getLogger(user.jid.bare)

        self.user_jid = user.jid
        self.user_pk = user.id

        self.ignore_messages = set[str]()

        self.contacts: LegacyRoster = LegacyRoster.get_self_or_unique_subclass()(self)
        self._logged = False
        self.__reset_ready()

        self.bookmarks: LegacyBookmarks = LegacyBookmarks.get_self_or_unique_subclass()(
            self
        )

        self.thread_creation_lock = asyncio.Lock()

        self.__cached_presence: Optional[CachedPresence] = None

        self.__tasks = set[asyncio.Task]()

    @property
    def user(self) -> GatewayUser:
        return self.xmpp.store.users.get(self.user_jid)  # type:ignore

    @property
    def http(self) -> aiohttp.ClientSession:
        return self.xmpp.http

    def __remove_task(self, fut):
        self.log.debug("Removing fut %s", fut)
        self.__tasks.remove(fut)

    def create_task(self, coro) -> asyncio.Task:
        task = self.xmpp.loop.create_task(coro)
        self.__tasks.add(task)
        self.log.debug("Creating task %s", task)
        task.add_done_callback(lambda _: self.__remove_task(task))
        return task

    def cancel_all_tasks(self):
        for task in self.__tasks:
            task.cancel()

    async def login(self) -> Optional[str]:
        """
        Logs in the gateway user to the legacy network.

        Triggered when the gateway start and on user registration.
        It is recommended that this function returns once the user is logged in,
        so if you need to await forever (for instance to listen to incoming events),
        it's a good idea to wrap your listener in an asyncio.Task.

        :return: Optionally, a text to use as the gateway status, e.g., "Connected as 'dude@legacy.network'"
        """
        raise NotImplementedError

    async def logout(self):
        """
        Logs out the gateway user from the legacy network.

        Called on gateway shutdown.
        """
        raise NotImplementedError

    async def on_text(
        self,
        chat: RecipientType,
        text: str,
        *,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional["Sender"] = None,
        thread: Optional[LegacyThreadType] = None,
        link_previews: Iterable[LinkPreview] = (),
        mentions: Optional[list[Mention]] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user sends a text message from XMPP to a bridged entity, e.g.
        to ``translated_user_name@slidge.example.com``, or ``translated_group_name@slidge.example.com``

        Override this and implement sending a message to the legacy network in this method.

        :param text: Content of the message
        :param chat: Recipient of the message. :class:`.LegacyContact` instance for 1:1 chat,
            :class:`.MUC` instance for groups.
        :param reply_to_msg_id: A legacy message ID if the message references (quotes)
            another message (:xep:`0461`)
        :param reply_to_fallback_text: Content of the quoted text. Not necessarily set
            by XMPP clients
        :param reply_to: Author of the quoted message. :class:`LegacyContact` instance for
            1:1 chat, :class:`LegacyParticipant` instance for groups.
            If `None`, should be interpreted as a self-reply if reply_to_msg_id is not None.
        :param link_previews: A list of sender-generated link previews.
            At the time of writing, only `Cheogram <https://wiki.soprani.ca/CheogramApp/LinkPreviews>`_
            supports it.
        :param mentions: (only for groups) A list of Contacts mentioned by their
            nicknames.
        :param thread:

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    send_text = deprecated("BaseSession.send_text", on_text)

    async def on_file(
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
        Triggered when the user sends a file using HTTP Upload (:xep:`0363`)

        :param url: URL of the file
        :param chat: See :meth:`.BaseSession.on_text`
        :param http_response: The HTTP GET response object on the URL
        :param reply_to_msg_id: See :meth:`.BaseSession.on_text`
        :param reply_to_fallback_text: See :meth:`.BaseSession.on_text`
        :param reply_to: See :meth:`.BaseSession.on_text`
        :param thread:

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    send_file = deprecated("BaseSession.send_file", on_file)

    async def on_sticker(
        self,
        chat: RecipientType,
        sticker: Sticker,
        *,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional[Union["LegacyContact", "LegacyParticipant"]] = None,
        thread: Optional[LegacyThreadType] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user sends a file using HTTP Upload (:xep:`0363`)

        :param chat: See :meth:`.BaseSession.on_text`
        :param sticker: The sticker sent by the user.
        :param reply_to_msg_id: See :meth:`.BaseSession.on_text`
        :param reply_to_fallback_text: See :meth:`.BaseSession.on_text`
        :param reply_to: See :meth:`.BaseSession.on_text`
        :param thread:

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def on_active(
        self, chat: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user sends an 'active' chat state (:xep:`0085`)

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        """
        raise NotImplementedError

    active = deprecated("BaseSession.active", on_active)

    async def on_inactive(
        self, chat: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user sends an 'inactive' chat state (:xep:`0085`)

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        """
        raise NotImplementedError

    inactive = deprecated("BaseSession.inactive", on_inactive)

    async def on_composing(
        self, chat: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user starts typing in a legacy chat (:xep:`0085`)

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        """
        raise NotImplementedError

    composing = deprecated("BaseSession.composing", on_composing)

    async def on_paused(
        self, chat: RecipientType, thread: Optional[LegacyThreadType] = None
    ):
        """
        Triggered when the user pauses typing in a legacy chat (:xep:`0085`)

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        """
        raise NotImplementedError

    paused = deprecated("BaseSession.paused", on_paused)

    async def on_displayed(
        self,
        chat: RecipientType,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user reads a message in a legacy chat. (:xep:`0333`)

        This is only possible if a valid ``legacy_msg_id`` was passed when
        transmitting a message from a legacy chat to the user, eg in
        :meth:`slidge.contact.LegacyContact.send_text`
        or
        :meth:`slidge.group.LegacyParticipant.send_text`.

        :param chat: See :meth:`.BaseSession.on_text`
        :param legacy_msg_id: Identifier of the message/
        :param thread:
        """
        raise NotImplementedError

    displayed = deprecated("BaseSession.displayed", on_displayed)

    async def on_correct(
        self,
        chat: RecipientType,
        text: str,
        legacy_msg_id: LegacyMessageType,
        *,
        thread: Optional[LegacyThreadType] = None,
        link_previews: Iterable[LinkPreview] = (),
        mentions: Optional[list[Mention]] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user corrects a message using :xep:`0308`

        This is only possible if a valid ``legacy_msg_id`` was returned by
        :meth:`.on_text`.

        :param chat: See :meth:`.BaseSession.on_text`
        :param text: The new text
        :param legacy_msg_id: Identifier of the edited message
        :param thread:
        :param link_previews: A list of sender-generated link previews.
            At the time of writing, only `Cheogram <https://wiki.soprani.ca/CheogramApp/LinkPreviews>`_
            supports it.
        :param mentions: (only for groups) A list of Contacts mentioned by their
            nicknames.
        """
        raise NotImplementedError

    correct = deprecated("BaseSession.correct", on_correct)

    async def on_react(
        self,
        chat: RecipientType,
        legacy_msg_id: LegacyMessageType,
        emojis: list[str],
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user sends message reactions (:xep:`0444`).

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        :param legacy_msg_id: ID of the message the user reacts to
        :param emojis: Unicode characters representing reactions to the message ``legacy_msg_id``.
            An empty string means "no reaction", ie, remove all reactions if any were present before
        """
        raise NotImplementedError

    react = deprecated("BaseSession.react", on_react)

    async def on_retract(
        self,
        chat: RecipientType,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
    ):
        """
        Triggered when the user retracts (:xep:`0424`) a message.

        :param chat: See :meth:`.BaseSession.on_text`
        :param thread:
        :param legacy_msg_id: Legacy ID of the retracted message
        """
        raise NotImplementedError

    retract = deprecated("BaseSession.retract", on_retract)

    async def on_presence(
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

    presence = deprecated("BaseSession.presence", on_presence)

    async def on_search(self, form_values: dict[str, str]) -> Optional[SearchResult]:
        """
        Triggered when the user uses Jabber Search (:xep:`0055`) on the component

        Form values is a dict in which keys are defined in :attr:`.BaseGateway.SEARCH_FIELDS`

        :param form_values: search query, defined for a specific plugin by overriding
            in :attr:`.BaseGateway.SEARCH_FIELDS`
        :return:
        """
        raise NotImplementedError

    search = deprecated("BaseSession.search", on_search)

    async def on_avatar(
        self,
        bytes_: Optional[bytes],
        hash_: Optional[str],
        type_: Optional[str],
        width: Optional[int],
        height: Optional[int],
    ) -> None:
        """
        Triggered when the user uses modifies their avatar via :xep:`0084`.

        :param bytes_: The data of the avatar. According to the spec, this
            should always be a PNG, but some implementations do not respect
            that. If `None` it means the user has unpublished their avatar.
        :param hash_: The SHA1 hash of the avatar data. This is an identifier of
            the avatar.
        :param type_: The MIME type of the avatar.
        :param width: The width of the avatar image.
        :param height: The height of the avatar image.
        """
        raise NotImplementedError

    async def on_moderate(
        self, muc: LegacyMUC, legacy_msg_id: LegacyMessageType, reason: Optional[str]
    ):
        """
        Triggered when the user attempts to retract a message that was sent in
        a MUC using :xep:`0425`.

        If retraction is not possible, this should raise the appropriate
        XMPPError with a human-readable message.

        NB: the legacy module is responsible for calling
        :method:`LegacyParticipant.moderate` when this is successful, because
        slidge will acknowledge the moderation IQ, but will not send the
        moderation message from the MUC automatically.

        :param muc: The MUC in which the message was sent
        :param legacy_msg_id: The legacy ID of the message to be retracted
        :param reason: Optionally, a reason for the moderation, given by the
            user-moderator.
        """
        raise NotImplementedError

    async def on_create_group(
        self, name: str, contacts: list[LegacyContact]
    ) -> LegacyGroupIdType:
        """
        Triggered when the user request the creation of a group via the
        dedicated :term:`Command`.

        :param name: Name of the group
        :param contacts: list of contacts that should be members of the group
        """
        raise NotImplementedError

    async def on_invitation(
        self, contact: LegacyContact, muc: LegacyMUC, reason: Optional[str]
    ):
        """
        Triggered when the user invites a :term:`Contact` to a legacy MUC via
        :xep:`0249`.

        The default implementation calls :meth:`LegacyMUC.on_set_affiliation`
        with the 'member' affiliation. Override if you want to customize this
        behaviour.

        :param contact: The invitee
        :param muc: The group
        :param reason: Optionally, a reason
        """
        await muc.on_set_affiliation(contact, "member", reason, None)

    async def on_leave_group(self, muc_legacy_id: LegacyGroupIdType):
        """
        Triggered when the user leaves a group via the dedicated slidge command
        or the :xep:`0077` ``<remove />`` mechanism.

        This should be interpreted as definitely leaving the group.

        :param muc_legacy_id: The legacy ID of the group to leave
        """
        raise NotImplementedError

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
        return f"<Session of {self.user_jid}>"

    def shutdown(self) -> asyncio.Task:
        for c in self.contacts:
            c.offline()
        for m in self.bookmarks:
            m.shutdown()
        return self.xmpp.loop.create_task(self.logout())

    @staticmethod
    def legacy_to_xmpp_msg_id(legacy_msg_id: LegacyMessageType) -> str:
        """
        Convert a legacy msg ID to a valid XMPP msg ID.
        Needed for read marks, retractions and message corrections.

        The default implementation just converts the legacy ID to a :class:`str`,
        but this should be overridden in case some characters needs to be escaped,
        or to add some additional,
        :term:`legacy network <Legacy Network`>-specific logic.

        :param legacy_msg_id:
        :return: A string that is usable as an XMPP stanza ID
        """
        return str(legacy_msg_id)

    legacy_msg_id_to_xmpp_msg_id = staticmethod(
        deprecated("BaseSession.legacy_msg_id_to_xmpp_msg_id", legacy_to_xmpp_msg_id)
    )

    @staticmethod
    def xmpp_to_legacy_msg_id(i: str) -> LegacyMessageType:
        """
        Convert a legacy XMPP ID to a valid XMPP msg ID.
        Needed for read marks and message corrections.

        The default implementation just converts the legacy ID to a :class:`str`,
        but this should be overridden in case some characters needs to be escaped,
        or to add some additional,
        :term:`legacy network <Legacy Network`>-specific logic.

        The default implementation is an identity function.

        :param i: The XMPP stanza ID
        :return: An ID that can be used to identify a message on the legacy network
        """
        return cast(LegacyMessageType, i)

    xmpp_msg_id_to_legacy_msg_id = staticmethod(
        deprecated("BaseSession.xmpp_msg_id_to_legacy_msg_id", xmpp_to_legacy_msg_id)
    )

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

        session = _sessions.get(user.jid.bare)
        if session is None:
            _sessions[user.jid.bare] = session = cls(user)
        return session

    @classmethod
    def from_user(cls, user):
        return cls._from_user_or_none(user)

    @classmethod
    def from_stanza(cls, s) -> "BaseSession":
        # """
        # Get a user's :class:`.LegacySession` using the "from" field of a stanza
        #
        # Meant to be called from :class:`BaseGateway` only.
        #
        # :param s:
        # :return:
        # """
        return cls.from_jid(s.get_from())

    @classmethod
    def from_jid(cls, jid: JID) -> "BaseSession":
        # """
        # Get a user's :class:`.LegacySession` using its jid
        #
        # Meant to be called from :class:`BaseGateway` only.
        #
        # :param jid:
        # :return:
        # """
        session = _sessions.get(jid.bare)
        if session is not None:
            return session
        user = cls.xmpp.store.users.get(jid)
        return cls._from_user_or_none(user)

    @classmethod
    async def kill_by_jid(cls, jid: JID):
        # """
        # Terminate a user session.
        #
        # Meant to be called from :class:`BaseGateway` only.
        #
        # :param jid:
        # :return:
        # """
        log.debug("Killing session of %s", jid)
        for user_jid, session in _sessions.items():
            if user_jid == jid.bare:
                break
        else:
            log.debug("Did not find a session for %s", jid)
            return
        for c in session.contacts:
            c.unsubscribe()
        user = cls.xmpp.store.users.get(jid)
        if user is None:
            log.warning("User not found during unregistration")
            return
        await cls.xmpp.unregister(user)
        cls.xmpp.store.users.delete(user.jid)
        del _sessions[user.jid.bare]
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
            pto=self.user_jid.bare, pstatus=status, pshow=show, **kwargs
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
        self.xmpp.send_text(text, mto=self.user_jid, **msg_kwargs)

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
        self.xmpp.invite_to(muc, reason=reason, password=password, mto=self.user_jid)

    async def input(self, text: str, **msg_kwargs):
        """
        Request user input via direct messages from the gateway component.

        Wraps call to :meth:`.BaseSession.input`

        :param text: The prompt to send to the user
        :param msg_kwargs: Extra attributes
        :return:
        """
        return await self.xmpp.input(self.user_jid, text, **msg_kwargs)

    async def send_qr(self, text: str):
        """
        Sends a QR code generated from 'text' via HTTP Upload and send the URL to
        ``self.user``

        :param text: Text to encode as a QR code
        """
        await self.xmpp.send_qr(text, mto=self.user_jid)

    def re_login(self):
        # Logout then re-login
        #
        # No reason to override this
        self.xmpp.re_login(self)

    async def get_contact_or_group_or_participant(self, jid: JID, create=True):
        if (contact := self.contacts.by_jid_only_if_exists(jid)) is not None:
            return contact
        if (muc := self.bookmarks.by_jid_only_if_exists(JID(jid.bare))) is not None:
            return await self.__get_muc_or_participant(muc, jid)
        else:
            muc = None

        if not create:
            return None

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
        # """
        # Wait until session, contacts and bookmarks are ready
        #
        # (slidge internal use)
        #
        # :param timeout:
        # :return:
        # """
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), timeout)
            await asyncio.wait_for(asyncio.shield(self.contacts.ready), timeout)
            await asyncio.wait_for(asyncio.shield(self.bookmarks.ready), timeout)
        except asyncio.TimeoutError:
            raise XMPPError(
                "recipient-unavailable",
                "Legacy session is not fully initialized, retry later",
            )

    def legacy_module_data_update(self, data: dict):
        with self.xmpp.store.session():
            user = self.user
            user.legacy_module_data.update(data)
            self.xmpp.store.users.update(user)

    def legacy_module_data_set(self, data: dict):
        with self.xmpp.store.session():
            user = self.user
            user.legacy_module_data = data
            self.xmpp.store.users.update(user)

    def legacy_module_data_clear(self):
        with self.xmpp.store.session():
            user = self.user
            user.legacy_module_data.clear()
            self.xmpp.store.users.update(user)


# keys = user.jid.bare
_sessions: dict[str, BaseSession] = {}
log = logging.getLogger(__name__)
