import functools
import logging
from typing import Generic, Optional, Type, Union, cast

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import XMPPError

from ..util import ABCSubclassableOnceAtMost, BiDict
from ..util.db import GatewayUser, user_store
from ..util.types import (
    BookmarksType,
    Chat,
    GatewayType,
    LegacyContactType,
    LegacyMessageType,
    LegacyMUCType,
    LegacyParticipantType,
    LegacyRosterType,
    PresenceShow,
    SessionType,
)
from ..util.util import SearchResult
from .contact import LegacyRoster
from .muc.bookmarks import LegacyBookmarks
from .muc.room import LegacyMUC


def ignore_sent_carbons(func):
    @functools.wraps(func)
    async def wrapped(self: SessionType, msg: Message):
        if (i := msg.get_id()) in self.ignore_messages:
            self.log.debug("Ignored sent carbon: %s", i)
            self.ignore_messages.remove(i)
        else:
            return await func(self, msg)

    return wrapped


def ignore_message_to_component(func):
    @functools.wraps(func)
    async def wrapped(self: SessionType, msg: Message):
        if msg.get_to() != self.xmpp.boundjid.bare:
            return await func(self, msg)
        else:
            log.debug("Ignoring message to component: %s %s", self, msg)

    return wrapped


class BaseSession(
    Generic[
        GatewayType,
        LegacyMessageType,
        LegacyRosterType,
        LegacyContactType,
        BookmarksType,
        LegacyMUCType,
        LegacyParticipantType,
    ],
    metaclass=ABCSubclassableOnceAtMost,
):
    """
    Represents a gateway user logged in to the legacy network and performing actions.

    Will be instantiated automatically when a user sends an online presence to the gateway
    component, as per :xep:`0100`.

    Must be subclassed for a functional slidge plugin.
    """

    sent: BiDict[LegacyMessageType, str]
    """
    Since we cannot set the XMPP ID of messages sent by XMPP clients, we need to keep a mapping
    between XMPP IDs and legacy message IDs if we want to further refer to a message that was sent
    by the user. This also applies to 'carboned' messages, ie, messages sent by the user from
    the official client of a legacy network.
    """

    xmpp: "GatewayType"
    """
    The gateway instance singleton. Use it for low-level XMPP calls or custom methods that are not
    session-specific.
    """

    def __init__(self, user: GatewayUser):
        self._roster_cls: Type[
            LegacyRosterType
        ] = LegacyRoster.get_self_or_unique_subclass()

        self.log = logging.getLogger(user.bare_jid)

        self.user = user
        self.sent = BiDict[LegacyMessageType, str]()  # TODO: set a max size for this
        # message ids (*not* stanza-ids), needed for last msg correction
        self.muc_sent_msg_ids = BiDict[LegacyMessageType, str]()

        self.ignore_messages = set[str]()

        self.contacts: LegacyRosterType = self._roster_cls(self)
        self.never_logged = True

        self.bookmarks: BookmarksType = LegacyBookmarks.get_self_or_unique_subclass()(
            self
        )

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
    def from_user(cls, user):
        return cls._from_user_or_none(user)

    @classmethod
    def from_stanza(cls: Type[SessionType], s) -> SessionType:
        """
        Get a user's :class:`.LegacySession` using the "from" field of a stanza

        Meant to be called from :class:`BaseGateway` only.

        :param s:
        :return:
        """
        return cls._from_user_or_none(user_store.get_by_stanza(s))

    @classmethod
    def from_jid(cls: Type[SessionType], jid: JID) -> SessionType:
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
        await session.logout()
        await cls.xmpp.unregister(user)
        del _sessions[user]
        del user
        del session

    @ignore_message_to_component
    @ignore_sent_carbons
    async def send_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        # we MUST not use `if m["replace"]["id"]` because it adds the tag if not
        # present. this is a problem for MUC echoed messages
        if m.xml.find("{urn:xmpp:message-correct:0}replace") is not None:
            # ignore last message correction (handled by a specific method)
            return
        if m.xml.find("{urn:xmpp:fasten:0}apply-to") is not None:
            # ignore message retraction (handled by a specific method)
            return

        e = await self.__get_entity(m)
        self.log.debug("Entity %r", e)

        if m.xml.find("{jabber:x:oob}x") is not None:
            url = m["oob"]["url"]
        else:
            url = None

        text = m["body"]
        if m.xml.find("{urn:xmpp:fallback:0}fallback") is not None and (
            isinstance(e, LegacyMUC) or e.REPLIES  # type: ignore
        ):
            text = m["feature_fallback"].get_stripped_body()
            reply_fallback = m["feature_fallback"].get_fallback_body()
        else:
            reply_fallback = None

        # Testing with `is None` is mandatory since a reply element have no
        # 'data' but only attributes, so the ElementTree is "false-ish".
        # Grrrrr this took me some time to figure out.
        reply_to = None
        if m.xml.find("{urn:xmpp:reply:0}reply") is not None:
            reply_to_msg_xmpp_id = self.__xmpp_msg_id_to_legacy(m["reply"]["id"])
            reply_to_jid = JID(m["reply"]["to"])
            if m["type"] == "chat":
                if reply_to_jid.bare != self.user.jid.bare:
                    try:
                        reply_to = await self.contacts.by_jid(reply_to_jid)
                    except XMPPError:
                        pass
            elif m["type"] == "groupchat":
                nick = reply_to_jid.resource
                try:
                    muc = await self.bookmarks.by_jid(reply_to_jid)
                except XMPPError:
                    pass
                else:
                    if nick != muc.user_nick:
                        reply_to = await muc.get_participant(reply_to_jid.resource)
        else:
            reply_to_msg_xmpp_id = None
            reply_to = None

        kwargs = dict(
            reply_to_msg_id=reply_to_msg_xmpp_id,
            reply_to_fallback_text=reply_fallback,
            reply_to=reply_to,
        )

        if url:
            legacy_msg_id = await self.send_file(url, e, **kwargs)
        elif text:
            legacy_msg_id = await self.send_text(text, e, **kwargs)
        else:
            log.debug("Ignoring %s", m)
            return

        if isinstance(e, LegacyMUC):
            await e.echo(m, legacy_msg_id)
            if legacy_msg_id is not None:
                self.muc_sent_msg_ids[legacy_msg_id] = m.get_id()
        else:
            if legacy_msg_id is not None:
                self.sent[legacy_msg_id] = m.get_id()

    async def __get_entity(self, m: Message) -> Union[LegacyContactType, LegacyMUCType]:
        if m.get_type() == "groupchat":
            muc = await self.bookmarks.by_jid(m.get_to())
            if m.get_from().resource not in muc.user_resources:
                raise XMPPError("not-acceptable", "You are not connected to this chat")
            return muc
        else:
            return await self.contacts.by_jid(m.get_to())

    @ignore_message_to_component
    async def active_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        await self.active(await self.__get_entity(m))

    @ignore_message_to_component
    async def inactive_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        await self.inactive(await self.__get_entity(m))

    @ignore_message_to_component
    async def composing_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        await self.composing(await self.__get_entity(m))

    @ignore_message_to_component
    async def paused_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        await self.paused(await self.__get_entity(m))

    def __xmpp_msg_id_to_legacy(self, xmpp_id: str):
        sent = self.sent.inverse.get(xmpp_id)
        if sent:
            return sent

        try:
            return self.xmpp_msg_id_to_legacy_msg_id(xmpp_id)
        except Exception as e:
            log.debug(
                "Couldn't convert xmpp msg ID to legacy ID, ignoring: %r, %s",
                e,
                e.args,
            )

    @ignore_message_to_component
    @ignore_sent_carbons
    async def displayed_from_msg(self, m: Message):
        """
        Meant to be called from :class:`BaseGateway` only.

        :param m:
        :return:
        """
        e = await self.__get_entity(m)
        displayed_msg_id = m["displayed"]["id"]
        if not isinstance(e, LegacyMUC) and self.xmpp.MARK_ALL_MESSAGES:
            to_mark = e.get_msg_xmpp_id_up_to(displayed_msg_id)  # type: ignore
            if to_mark is None:
                log.debug("Can't mark all messages up to %s", displayed_msg_id)
                to_mark = [displayed_msg_id]
        else:
            to_mark = [displayed_msg_id]
        for xmpp_id in to_mark:
            if legacy := self.__xmpp_msg_id_to_legacy(xmpp_id):
                await self.displayed(legacy, e)
                if isinstance(e, LegacyMUC):
                    await e.echo(m, None)
            else:
                log.debug("Ignored displayed marker for msg: %r", xmpp_id)

    @ignore_message_to_component
    @ignore_sent_carbons
    async def correct_from_msg(self, m: Message):
        e = await self.__get_entity(m)
        xmpp_id = m["replace"]["id"]
        if isinstance(e, LegacyMUC):
            legacy_id = self.muc_sent_msg_ids.inverse.get(xmpp_id)
        else:
            legacy_id = self.__xmpp_msg_id_to_legacy(xmpp_id)

        if legacy_id is None:
            log.debug("Did not find legacy ID to correct")
            new_legacy_msg_id = await self.send_text(m["body"], e)
        else:
            new_legacy_msg_id = await self.correct(m["body"], legacy_id, e)
        if isinstance(e, LegacyMUC):
            if new_legacy_msg_id is not None:
                self.muc_sent_msg_ids[new_legacy_msg_id] = m.get_id()
            await e.echo(m, new_legacy_msg_id)
        else:
            if new_legacy_msg_id is not None:
                self.sent[new_legacy_msg_id] = m.get_id()

    @ignore_message_to_component
    @ignore_sent_carbons
    async def react_from_msg(self, m: Message):
        e = await self.__get_entity(m)
        react_to: str = m["reactions"]["id"]
        legacy_id = self.__xmpp_msg_id_to_legacy(react_to)

        if not legacy_id:
            log.debug("Ignored reaction from user")
            raise XMPPError("internal-server-error")

        emojis = [
            remove_emoji_variation_selector_16(r["value"]) for r in m["reactions"]
        ]
        error_msg = None

        if e.REACTIONS_SINGLE_EMOJI and len(emojis) > 1:
            error_msg = "Maximum 1 emoji/message"

        if not error_msg and (subset := await e.available_emojis(legacy_id)):
            log.debug("%s %s %s", set(emojis), subset, set(emojis).issubset(subset))
            if not set(emojis).issubset(subset):
                error_msg = (
                    f"You can only react with the following emojis: {''.join(subset)}"
                )

        if error_msg:
            self.send_gateway_message(error_msg)
            if not isinstance(e, LegacyMUC):
                # no need to carbon for groups, we just don't echo the stanza
                e.react(legacy_id, carbon=True)  # type: ignore
            await self.react(legacy_id, [], e)
            raise XMPPError("not-acceptable", text=error_msg)

        await self.react(legacy_id, emojis, e)
        if isinstance(e, LegacyMUC):
            await e.echo(m, None)

    @ignore_message_to_component
    @ignore_sent_carbons
    async def retract_from_msg(self, m: Message):
        e = await self.__get_entity(m)
        xmpp_id: str = m["apply_to"]["id"]
        legacy_id = self.__xmpp_msg_id_to_legacy(xmpp_id)
        if legacy_id:
            await self.retract(legacy_id, e)
            if isinstance(e, LegacyMUC):
                await e.echo(m, None)
        else:
            log.debug("Ignored retraction from user")

    async def join_groupchat(self, p: Presence):
        if not self.xmpp.GROUPS:
            raise XMPPError(
                "not-implemented", "This gateway does not implement multi-user chats."
            )
        muc = await self.bookmarks.by_jid(p.get_to())
        log.debug("BOOKMARKS: %r", self.bookmarks.__class__)
        log.debug("JOIN MUC: %r -- %r -- %r", muc, muc.join, muc.__class__)
        await muc.join(p)

    def send_gateway_status(
        self,
        status: Optional[str] = None,
        show=Optional[PresenceShow],
        **kwargs,
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
        self.xmpp.send_message(
            mto=self.user.jid, mbody=text, mfrom=self.xmpp.boundjid, **msg_kwargs
        )

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
        text: str,
        chat: Chat,
        *,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional[Union["LegacyContactType", "LegacyParticipantType"]] = None,
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

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def send_file(
        self,
        url: str,
        chat: Chat,
        *,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to: Optional[Union[LegacyContactType, "LegacyParticipantType"]] = None,
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user has sends a file using HTTP Upload (:xep:`0363`)

        :param url: URL of the file
        :param chat: See :meth:`.BaseSession.send_text`
        :param reply_to_msg_id: See :meth:`.BaseSession.send_text`
        :param reply_to_fallback_text: See :meth:`.BaseSession.send_text`
        :param reply_to: See :meth:`.BaseSession.send_text`

        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: Chat):
        """
        Triggered when the user sends an 'active' chat state to the legacy network (:xep:`0085`)

        :param c: Recipient of the active chat state
        """
        raise NotImplementedError

    async def inactive(self, c: Chat):
        """
        Triggered when the user sends an 'inactive' chat state to the legacy network (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def composing(self, c: Chat):
        """
        Triggered when the user starts typing in the window of a legacy contact (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def paused(self, c: Chat):
        """
        Triggered when the user pauses typing in the window of a legacy contact (:xep:`0085`)

        :param c:
        """
        raise NotImplementedError

    async def displayed(self, legacy_msg_id: LegacyMessageType, c: Chat):
        """
        Triggered when the user reads a message sent by a legacy contact.  (:xep:`0333`)

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.sent_text` or :meth:`slidge.LegacyContact.send_file`.

        :param legacy_msg_id: Identifier of the message, passed to :meth:`slidge.LegacyContact.send_text`
            or :meth:`slidge.LegacyContact.send_file`
        :param c:
        """
        raise NotImplementedError

    async def correct(
        self, text: str, legacy_msg_id: LegacyMessageType, c: Chat
    ) -> Optional[LegacyMessageType]:
        """
        Triggered when the user corrected a message using :xep:`0308`

        This is only possible if a valid ``legacy_msg_id`` was passed when transmitting a message
        from a contact to the user in :meth:`.LegacyContact.send_text` or :meth:`slidge.LegacyContact.send_file`.

        :param text:
        :param legacy_msg_id:
        :param c:
        """
        raise NotImplementedError

    async def search(self, form_values: dict[str, str]) -> Optional["SearchResult"]:
        """
        Triggered when the user uses Jabber Search (:xep:`0055`) on the component

        Form values is a dict in which keys are defined in :attr:`.BaseGateway.SEARCH_FIELDS`

        :param form_values: search query, defined for a specific plugin by overriding
            in :attr:`.BaseGateway.SEARCH_FIELDS`
        :return:
        """
        raise NotImplementedError

    async def react(self, legacy_msg_id: LegacyMessageType, emojis: list[str], c: Chat):
        """
        Triggered when the user sends message reactions (:xep:`0444`).

        :param legacy_msg_id: ID of the message the user reacts to
        :param emojis: Unicode characters representing reactions to the message ``legacy_msg_id``.
            An empty string means "no reaction", ie, remove all reactions if any were present before
        :param c: Contact or MUC the reaction refers to
        """
        raise NotImplementedError

    async def retract(self, legacy_msg_id: LegacyMessageType, c: Chat):
        """
        Triggered when the user retracts (:xep:`0424`) a message.

        :param legacy_msg_id: Legacy ID of the retracted message
        :param c: The contact this retraction refers to
        """
        raise NotImplementedError


def remove_emoji_variation_selector_16(emoji: str):
    # this is required for compatibility with dino, and maybe other future clients?
    return bytes(emoji, encoding="utf-8").replace(b"\xef\xb8\x8f", b"").decode()


_sessions: dict[GatewayUser, BaseSession] = {}
log = logging.getLogger(__name__)
