import asyncio
import logging
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    Any,
    Generic,
    Iterable,
    Literal,
    Optional,
    Type,
    TypeVar,
    Union,
)

from slixmpp import JID, Iq, Message

from ..util import SubclassableOnce
from ..util.types import (
    AvatarType,
    LegacyContactIdType,
    LegacyMessageType,
    LegacyUserIdType,
)

if TYPE_CHECKING:
    from .session import SessionType
else:
    SessionType = TypeVar("SessionType")


class LegacyContact(Generic[SessionType], metaclass=SubclassableOnce):
    """
    This class centralizes actions in relation to a specific legacy contact.

    You shouldn't create instances of contacts manually, but rather rely on
    :meth:`.LegacyRoster.by_legacy_id` to ensure that contact instances are
    singletons. The :class:`.LegacyRoster` instance of a session is accessible
    through the :attr:`.BaseSession.contacts` attribute.

    Typically, your plugin should have methods hook to the legacy events and
    call appropriate methods here to transmit the "legacy action" to the xmpp
    user. This should look like this:

    .. code-block:python

        class Session(BaseSession):
            ...

            async def on_cool_chat_network_new_text_message(self, legacy_msg_event):
                contact = self.contacts.by_legacy_id(legacy_msg_event.from)
                contact.send_text(legacy_msg_event.text)

            async def on_cool_chat_network_new_typing_event(self, legacy_typing_event):
                contact = self.contacts.by_legacy_id(legacy_msg_event.from)
                contact.composing()
            ...
    """

    RESOURCE: str = "slidge"
    """
    A full JID, including a resource part is required for chat states (and maybe other stuff)
    to work properly. This is the name of the resource the contacts will use.
    """
    FEATURES = {
        "http://jabber.org/protocol/chatstates",
        "urn:xmpp:receipts",
        "vcard-temp",
        "jabber:x:oob",
        "urn:xmpp:message-correct:0",
        "urn:xmpp:reactions:0",
    }
    """
    A list of features advertised through service discovery and client capabilities.
    """

    def __init__(
        self,
        session: "SessionType",
        legacy_id: LegacyContactIdType,
        jid_username: str,
    ):
        """
        :param session: The session this contact is part of
        :param legacy_id: The contact's legacy ID
        :param jid_username: User part of this contact's 'puppet' JID.
            NB: case-insensitive, and some special characters are not allowed
        """
        self.session = session
        self.user = session.user
        self.legacy_id = legacy_id
        self.jid_username = jid_username

        self.added_to_roster = False

        self._name: Optional[str] = None
        self._avatar: Optional[AvatarType] = None

        self.xmpp = session.xmpp
        asyncio.create_task(self.__make_caps())
        asyncio.create_task(self.__make_vcard())

    def __repr__(self):
        return f"<LegacyContact <{self.jid}> ('{self.legacy_id}') of <{self.user}>"

    async def __make_caps(self):
        """
        Configure slixmpp to correctly advertise this contact's capabilities.
        """
        jid = self.jid
        xmpp = self.xmpp

        xmpp["xep_0030"].add_identity(jid=jid, category="client", itype="bot")
        for f in self.FEATURES:
            await xmpp["xep_0030"].add_feature(feature=f, jid=jid)

        info = await xmpp["xep_0030"].get_info(jid, node=None, local=True)
        if isinstance(info, Iq):
            info = info["disco_info"]
        ver = xmpp["xep_0115"].generate_verstring(info, xmpp["xep_0115"].hash)
        await xmpp["xep_0030"].set_info(
            jid=jid,
            node="%s#%s" % (xmpp["xep_0115"].caps_node, ver),
            info=info,
        )

        await xmpp["xep_0115"].cache_caps(ver, info)
        await xmpp["xep_0115"].assign_verstring(jid, ver)

    async def __make_vcard(self):
        """
        Configure slixmpp to correctly set this contact's vcard (in fact only its avatar ATM)
        """
        await self.xmpp.set_vcard_avatar(jid=self.jid, avatar=self.avatar)

    @property
    def jid(self) -> JID:
        """
        Full JID (including the 'puppet' resource) of the contact
        """
        j = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        j.resource = self.RESOURCE
        return j

    @property
    def name(self):
        """
        Friendly name of the contact, as it should appear in the user's roster
        """
        return self._name

    @name.setter
    def name(self, n: Optional[str]):
        self._name = n

    @property
    def avatar(self):
        """
        An image that represents this contact
        """
        return self._avatar

    @avatar.setter
    def avatar(self, a: Optional[AvatarType]):
        self._avatar = a
        self.xmpp.loop.create_task(self.__make_vcard())

    async def add_to_roster(self):
        """
        Add this contact to the user roster using :xep:`0356`
        """
        await self.xmpp["xep_0356"].set_roster(
            jid=self.user.jid,
            roster_items={
                self.jid.bare: {
                    "name": self.name,
                    "subscription": "both",
                    "groups": [self.xmpp.ROSTER_GROUP],
                }
            },
        )
        self.added_to_roster = True

    def online(self, status: Optional[str] = None):
        """
        Send an "online" presence from this contact to the user.

        :param status: Arbitrary text, details of the status, eg: "Listening to Britney Spears"
        """
        self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, pstatus=status)

    def away(self, status: Optional[str] = None):
        """
        Send an "away" presence from this contact to the user.

        This is a global status, as opposed to :meth:`.LegacyContact.inactive`
        which concerns a specific conversation, ie a specific "chat window"

        :param status: Arbitrary text, details of the status, eg: "Gone to fight capitalism"
        """
        self.xmpp.send_presence(
            pfrom=self.jid, pto=self.user.jid.bare, pshow="away", pstatus=status
        )

    def busy(self, status: Optional[str] = None):
        """
        Send a "busy" presence from this contact to the user,

        :param status: eg: "Trying to make sense of XEP-0100"
        """
        self.xmpp.send_presence(
            pfrom=self.jid, pto=self.user.jid.bare, pshow="busy", pstatus=status
        )

    def offline(self):
        """
        Send an "offline" presence from this contact to the user.
        """
        self.xmpp.send_presence(
            pfrom=self.jid, pto=self.user.jid.bare, ptype="unavailable"
        )

    def unsubscribe(self):
        """
        Send an "unsubscribed" presence from this contact to the user.
        """
        self.xmpp.send_presence(
            pfrom=self.jid, pto=self.user.jid.bare, ptype="unsubscribed"
        )

    def status(self, text: str):
        """
        Set a contact's status
        """
        self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, pstatus=text)

    def __chat_state(self, state: str):
        msg = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, mtype="chat")
        msg["chat_state"] = state
        msg.enable("no-store")
        msg.send()

    def active(self):
        """
        Send an "active" chat state (:xep:`0085`) from this contact to the user.
        """
        self.__chat_state("active")

    def composing(self):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self.__chat_state("composing")

    def paused(self):
        """
        Send a "paused" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self.__chat_state("paused")

    def inactive(self):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        log.debug("%s go inactive", self)
        self.__chat_state("inactive")

    def __send_marker(
        self,
        legacy_msg_id: LegacyMessageType,
        marker: Literal["acknowledged", "received", "displayed"],
    ):
        """
        Send a message marker (:xep:`0333`) from this contact to the user.

        NB: for the 'received' marker, this also sends a message receipt (:xep:`0184`)

        :param legacy_msg_id: ID of the message this marker refers to
        :param marker: The marker type

        """
        xmpp_id = self.session.sent.get(legacy_msg_id)
        if xmpp_id is None:
            log.debug("Cannot find the XMPP ID of this msg: %s", legacy_msg_id)
        else:
            if marker == "received":
                receipt = self.xmpp.Message()
                receipt["to"] = self.user.jid
                receipt["receipt"] = xmpp_id
                receipt["from"] = self.jid
                receipt.send()
            self.xmpp["xep_0333"].send_marker(
                mto=self.user.jid,
                id=xmpp_id,
                marker=marker,
                mfrom=self.jid,
            )

    def ack(self, legacy_msg_id: LegacyMessageType):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self.__send_marker(legacy_msg_id, "acknowledged")

    def received(self, legacy_msg_id: LegacyMessageType):
        """
        Send a "received" message marker (:xep:`0333`) and a "message delivery receipt"
        (:xep:`0184`)
        from this contact to the user

        :param legacy_msg_id: The message this marker refers to
        """
        self.__send_marker(legacy_msg_id, "received")

    def displayed(self, legacy_msg_id: LegacyMessageType):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self.__send_marker(legacy_msg_id, "displayed")

    def __make_message(self, **kwargs) -> Message:
        m = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, **kwargs)
        m.enable("markable")
        return m

    def __send_message(self, msg: Message, legacy_msg_id: Optional[Any] = None):
        if legacy_msg_id is not None:
            msg.set_id(self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id))
        msg.send()

    def send_text(
        self,
        body: str = "",
        chat_state: Optional[str] = "active",
        legacy_msg_id: Optional[LegacyMessageType] = None,
    ) -> Message:
        """
        Transmit a message from the contact to the user

        :param body: Context of the message
        :param chat_state: By default, will send an "active" chat state (:xep:`0085`) along with the
            message. Set this to ``None`` if this is not desired.
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this

        :return: the XMPP message that was sent
        """
        msg = self.__make_message(mbody=body, mtype="chat")
        if chat_state is not None:
            msg["chat_state"] = chat_state
        self.__send_message(msg, legacy_msg_id)
        return msg

    async def send_file(
        self,
        filename: Union[Path, str],
        content_type: Optional[str] = None,
        input_file: Optional[IO[bytes]] = None,
        legacy_msg_id: Optional[LegacyMessageType] = None,
    ) -> Message:
        """
        Send a file using HTTP upload (:xep:`0363`)

        :param filename: Filename to use or location on disk to the file to upload
        :param content_type: MIME type, inferred from filename if not given
        :param input_file: Optionally, a file like object instead of a file on disk.
            filename will still be used to give the uploaded file a name
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        """
        log.debug("HOST: %s", self.xmpp.server_host)
        url = await self.xmpp["xep_0363"].upload_file(
            filename=filename,
            content_type=content_type,
            input_file=input_file,
        )
        msg = self.__make_message()
        msg["oob"]["url"] = url
        msg["body"] = url
        self.__send_message(msg, legacy_msg_id)
        return msg

    def __carbon(self, msg: Message):
        carbon = Message()
        carbon["from"] = self.user.jid
        carbon["type"] = "chat"
        carbon["carbon_sent"] = msg

        from_ = copy(self.user.jid)
        from_.resource = "slidge"
        msg["from"] = from_
        for resource in self.xmpp.client_roster.presence(self.user.jid):
            to = copy(self.user.jid)
            to.resource = resource
            carbon["to"] = str(to)
            self.xmpp["xep_0356"].send_privileged_message(copy(carbon))

    def carbon(
        self,
        body: str,
        legacy_id: Optional[Any] = None,
        date: Optional[datetime] = None,
    ):
        """
        Sync a message sent using an official client by the gateway user to a legacy contact.

        Uses xep:`0356` to impersonate the XMPP user and send a carbon message (:xep:`0280`).

        :param str body: Body of the message.
        :param legacy_id: Legacy message ID
        :param str date: When was this message sent.
        """
        # we use Message() directly because we need xmlns="jabber:client"
        msg = Message()
        msg["to"] = self.jid.bare
        msg["type"] = "chat"
        msg["body"] = body
        if legacy_id:
            xmpp_id = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_id)
            msg.set_id(xmpp_id)
            self.session.sent[legacy_id] = xmpp_id
        if date:
            if date.tzinfo is None:
                date = date.astimezone(timezone.utc)
            msg["delay"].set_stamp(date)

        self.__carbon(msg)
        return msg.get_id()

    def carbon_read(self, legacy_msg_id: Any, date: Optional[datetime] = None):
        """
        Synchronize user read state from official clients.

        Uses xep:`0356` to impersonate the XMPP user and send a carbon message (:xep:`0280`).

        :param str legacy_msg_id:
        :param str date:
        """
        # we use Message() directly because we need xmlns="jabber:client"
        msg = Message()
        msg["to"] = self.jid.bare
        msg["type"] = "chat"
        msg["displayed"]["id"] = self.session.legacy_msg_id_to_xmpp_msg_id(
            legacy_msg_id
        )
        if date is not None:
            if date.tzinfo is None:
                date = date.astimezone(timezone.utc)
            msg["delay"].set_stamp(date)

        self.__carbon(msg)

    def carbon_react(
        self, legacy_msg_id: LegacyMessageType, reactions: Iterable[str] = ()
    ):
        """
        Call this to modify the user's own reactions (:xep:`0444`) about a message.

        Can be called when the user reacts from the official client, or to modify a user's
        reaction when the legacy network has constraints about acceptable reactions.

        :param legacy_msg_id: Legacy message ID this refers to
        :param reactions: iterable of emojis
        """
        msg = Message()
        msg["to"] = self.jid.bare
        msg["type"] = "chat"
        self.xmpp["xep_0444"].set_reactions(
            msg,
            to_id=self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id),
            reactions=reactions,
        )
        self.__carbon(msg)

    def correct(self, legacy_msg_id: Any, new_text: str):
        """
        Call this when a legacy contact has modified his last message content.

        Uses last message correction (:xep:`0308`)

        :param legacy_msg_id: Legacy message ID this correction refers to
        :param new_text: The new text
        """
        msg = self.__make_message()
        msg["replace"]["id"] = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id)
        msg["body"] = new_text
        self.__send_message(msg)

    def react(self, legacy_msg_id: LegacyMessageType, emojis: Iterable[str]):
        """
        Call this when a legacy contact reacts to a message

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: A iterable of emojis used as reactions
        :return:
        """
        if (xmpp_id := self.session.sent.get(legacy_msg_id)) is None:
            log.debug(
                "Cannot determine which message this reaction refers to, attempting msg ID conversion"
            )
            xmpp_id = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id)
        msg = self.__make_message()
        self.xmpp["xep_0444"].set_reactions(
            msg,
            to_id=xmpp_id,
            reactions=emojis,
        )
        self.__send_message(msg)
        return msg


LegacyContactType = TypeVar("LegacyContactType", bound=LegacyContact)


class LegacyRoster(Generic[LegacyContactType, SessionType], metaclass=SubclassableOnce):
    """
    Virtual roster of a gateway user, that allows to represent all
    of their contacts as singleton instances (if used properly and not too bugged).

    Every :class:`.BaseSession` instance will have its own :class:`.LegacyRoster` instance
    accessible via the :attr:`.BaseSession.contacts` attribute.

    Typically, you will mostly use the :meth:`.LegacyRoster.by_legacy_id` function to
    retrieve a contact instance.

    You might need to override :meth:`.LegacyRoster.legacy_id_to_jid_username` and/or
    :meth:`.LegacyRoster.jid_username_to_legacy_id` to incorporate some custom logic
    if you need some characters when translation JID user parts and legacy IDs.
    """

    def __init__(self, session: "SessionType"):
        self._contact_cls: Type[
            LegacyContactType
        ] = LegacyContact.get_self_or_unique_subclass()
        self._contact_cls.xmpp = session.xmpp

        self.session = session
        self._contacts_by_bare_jid: dict[str, LegacyContactType] = {}
        self._contacts_by_legacy_id: dict[LegacyContactIdType, LegacyContactType] = {}

    def __iter__(self):
        return iter(self._contacts_by_legacy_id.values())

    def by_jid(self, contact_jid: JID) -> LegacyContactType:
        """
        Retrieve a contact by their JID

        If the contact was not instantiated before, it will be created
        using :meth:`slidge.LegacyRoster.jid_username_to_legacy_id` to infer their
        legacy user ID.

        :param contact_jid:
        :return:
        """
        bare = contact_jid.bare
        c = self._contacts_by_bare_jid.get(bare)
        if c is None:
            jid_username = str(contact_jid.username)
            log.debug("Contact %s not found", contact_jid)
            c = self._contact_cls(
                self.session,
                self.jid_username_to_legacy_id(jid_username),
                jid_username,
            )
            self._contacts_by_bare_jid[bare] = c
        return c

    def by_legacy_id(self, legacy_id: Any) -> LegacyContactType:
        """
        Retrieve a contact by their legacy_id

        If the contact was not instantiated before, it will be created
        using :meth:`slidge.LegacyRoster.legacy_id_to_jid_username` to infer their
        legacy user ID.

        :param legacy_id:
        :return:
        """
        c = self._contacts_by_legacy_id.get(legacy_id)
        if c is None:
            log.debug("Contact %s not found in roster", legacy_id)
            c = self._contact_cls(
                self.session, legacy_id, self.legacy_id_to_jid_username(legacy_id)
            )
            self._contacts_by_legacy_id[legacy_id] = c
        return c

    def by_stanza(self, s) -> LegacyContactType:
        """
        Retrieve a contact by the destination of a stanza

        See :meth:`slidge.Roster.by_legacy_id` for more info.

        :param s:
        :return:
        """
        return self.by_jid(s.get_to())

    @staticmethod
    def legacy_id_to_jid_username(legacy_id: Any) -> str:
        """
        Convert a legacy ID to a valid 'user' part of a JID

        Should be overridden for cases where the str conversion of
        the legacy_id is not enough, e.g., if it contains forbidden character.

        :param legacy_id:
        """
        return str(legacy_id)

    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> LegacyUserIdType:
        """
        Convert a JID user part to a legacy ID.

        Should be overridden in case legacy IDs are not strings, or more generally
        for any case where the username part of a JID is not enough to identify
        a contact on the legacy network.

        Default implementation is an identity operation

        :param jid_username: User part of a JID, ie "user" in "user@example.com"
        :return: An identifier for the user on the legacy network.
        """
        return jid_username  # type:ignore


LegacyRosterType = TypeVar("LegacyRosterType", bound=LegacyRoster)

log = logging.getLogger(__name__)
