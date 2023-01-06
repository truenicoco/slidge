import logging
from datetime import date, datetime
from typing import Any, Generic, Optional, Type, Union

from slixmpp import JID, Message, Presence
from slixmpp.jid import JID_UNESCAPE_TRANSFORMATIONS, _unescape_node

from ..util import SubclassableOnce
from ..util.types import (
    AvatarType,
    LegacyContactType,
    LegacyMessageType,
    LegacyUserIdType,
    SessionType,
)
from ..util.xep_0292.stanza import VCard4
from . import config
from .mixins import FullCarbonMixin
from .mixins.base import ReactionRecipientMixin


class LegacyContact(
    Generic[SessionType, LegacyUserIdType],
    FullCarbonMixin,
    ReactionRecipientMixin,
    metaclass=SubclassableOnce,
):
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

    Use ``carbon=True`` as a keyword arg for methods to represent an action FROM
    the user TO the contact, typically when the user uses an official client to
    do an action such as sending a message or marking as message as read.
    This will use :xep:`0363` to impersonate the XMPP user in order.
    """

    session: "SessionType"

    RESOURCE: str = "slidge"
    """
    A full JID, including a resource part is required for chat states (and maybe other stuff)
    to work properly. This is the name of the resource the contacts will use.
    """

    mtype = "chat"
    is_group = False

    def __init__(
        self,
        session: "SessionType",
        legacy_id: LegacyUserIdType,
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

        self._subscribe_from = True
        self._subscribe_to = True

        if self.xmpp.MARK_ALL_MESSAGES:
            self._sent_order = list[str]()

        self.xmpp = session.xmpp
        self.jid = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        self.jid.resource = self.RESOURCE

    def __repr__(self):
        return f"<LegacyContact <{self.jid}> ('{self.legacy_id}') of <{self.user}>"

    def __get_subscription_string(self):
        if self._subscribe_from and self._subscribe_to:
            return "both"
        if self._subscribe_from:
            return "from"
        if self._subscribe_to:
            return "to"
        return "none"

    def _send(self, stanza: Union[Message, Presence], carbon=False, **send_kwargs):
        if carbon and isinstance(stanza, Message):
            stanza["to"] = self.jid.bare
            stanza["from"] = self.user.jid
            self._privileged_send(stanza)
        else:
            if self.xmpp.MARK_ALL_MESSAGES and is_markable(stanza):
                self._sent_order.append(stanza["id"])
            stanza["to"] = self.user.jid
            stanza.send()

    def get_msg_xmpp_id_up_to(self, horizon_xmpp_id: str):
        """
        Return XMPP msg ids sent by this contact up to a given XMPP msg id.

        Plugins have no reason to use this, but it is used by slidge core
        for legacy networks that need to mark all messages as read (most XMPP
        clients only send a read marker for the latest message.

        This has side effects, if the horizon XMPP id is found, messages up to
        this horizon are not cleared, to avoid sending the same read mark twice.

        :param horizon_xmpp_id: The latest message
        :return: A list of XMPP ids or None if horizon_xmpp_id was not found
        """
        for i, xmpp_id in enumerate(self._sent_order):
            if xmpp_id == horizon_xmpp_id:
                break
        else:
            return
        i += 1
        res = self._sent_order[:i]
        self._sent_order = self._sent_order[i:]
        return res

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_self=False,
        **kwargs,
    ):
        """
        The contact sends a message to the user.

        :param body:
        :param legacy_msg_id:
        :param when:
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_self: Set to true is this is a self quote. If False, it means the
            quoted author is the gateway user.
        """
        super().send_text(
            body=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=self.jid if reply_self else self.user.jid,
            **kwargs,
        )

    @property
    def name(self):
        """
        Friendly name of the contact, as it should appear in the user's roster
        """
        return self._name

    @name.setter
    def name(self, n: Optional[str]):
        if self._name == n:
            return
        self._name = n
        self.xmpp.pubsub.set_nick(
            jid=self.jid.bare, nick=n, restrict_to=self.user.jid.bare
        )

    @property
    def avatar(self):
        """
        An image that represents this contact
        """
        return self._avatar

    @avatar.setter
    def avatar(self, a: Optional[AvatarType]):
        if a == self._avatar:
            return
        self.xmpp.loop.create_task(
            self.xmpp.pubsub.set_avatar(
                jid=self.jid.bare, avatar=a, restrict_to=self.user.jid.bare
            )
        )
        self._avatar = a

    def set_vcard(
        self,
        /,
        full_name: Optional[str] = None,
        given: Optional[str] = None,
        surname: Optional[str] = None,
        birthday: Optional[date] = None,
        phone: Optional[str] = None,
        note: Optional[str] = None,
        url: Optional[str] = None,
        email: Optional[str] = None,
        country: Optional[str] = None,
        locality: Optional[str] = None,
    ):
        vcard = VCard4()
        vcard.add_impp(f"xmpp:{self.jid.bare}")

        if n := self.name:
            vcard.add_nickname(n)
        if full_name:
            vcard["full_name"] = full_name
        elif n:
            vcard["full_name"] = n

        if given:
            vcard["given"] = given
        if surname:
            vcard["surname"] = surname
        if birthday:
            vcard["birthday"] = birthday

        if note:
            vcard.add_note(note)
        if url:
            vcard.add_url(url)
        if email:
            vcard.add_email(email)
        if phone:
            vcard.add_tel(phone)
        if country and locality:
            vcard.add_address(country, locality)
        elif country:
            vcard.add_address(country, locality)

        self.xmpp.vcard.set_vcard(self.jid.bare, vcard, {self.user.jid.bare})

    async def add_to_roster(self):
        """
        Add this contact to the user roster using :xep:`0356`
        """
        if config.NO_ROSTER_PUSH:
            log.debug("Roster push request by plugin ignored (--no-roster-push)")
            return
        item = {
            "subscription": self.__get_subscription_string(),
            "groups": [self.xmpp.ROSTER_GROUP],
        }
        if (n := self.name) is not None:
            item["name"] = n
        kw = dict(
            jid=self.user.jid,
            roster_items={self.jid.bare: item},
        )
        try:
            await self.xmpp["xep_0356"].set_roster(**kw)
        except PermissionError:
            try:
                await self.xmpp["xep_0356_old"].set_roster(**kw)
            except PermissionError:
                log.warning(
                    "Slidge does not have privileges to add contacts to the roster."
                    "Refer to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html "
                    "for more info."
                )
                if config.ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK:
                    self._send(self._make_presence(ptype="subscribe"))
                return

        self.added_to_roster = True
        self._send_last_presence()

    def unsubscribe(self):
        """
        Send an "unsubscribe", "unsubscribed", "unavailable" presence sequence
        from this contact to the user, ie, "this contact has removed you from
        their 'friends'".
        """
        for ptype in "unsubscribe", "unsubscribed", "unavailable":
            self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, ptype=ptype)  # type: ignore


class LegacyRoster(
    Generic[SessionType, LegacyContactType, LegacyUserIdType],
    metaclass=SubclassableOnce,
):
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
        self._contacts_by_legacy_id: dict[LegacyUserIdType, LegacyContactType] = {}

    def __iter__(self):
        return iter(self._contacts_by_legacy_id.values())

    def known_contacts(self):
        return self._contacts_by_bare_jid.copy()

    async def by_jid(self, contact_jid: JID) -> LegacyContactType:
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
                await self.jid_username_to_legacy_id(jid_username),
                jid_username,
            )
            await c.update_caps()
            self._contacts_by_legacy_id[c.legacy_id] = self._contacts_by_bare_jid[
                bare
            ] = c
        return c

    async def by_legacy_id(self, legacy_id: Any) -> LegacyContactType:
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
                self.session, legacy_id, await self.legacy_id_to_jid_username(legacy_id)
            )
            await c.update_caps()
            self._contacts_by_bare_jid[c.jid.bare] = self._contacts_by_legacy_id[
                legacy_id
            ] = c
        return c

    async def by_stanza(self, s) -> LegacyContactType:
        """
        Retrieve a contact by the destination of a stanza

        See :meth:`slidge.Roster.by_legacy_id` for more info.

        :param s:
        :return:
        """
        return await self.by_jid(s.get_to())

    async def legacy_id_to_jid_username(self, legacy_id: Any) -> str:
        """
        Convert a legacy ID to a valid 'user' part of a JID

        Should be overridden for cases where the str conversion of
        the legacy_id is not enough, e.g., if it is case-sensitive or contains
        forbidden characters not covered by :xep:`0106`.

        :param legacy_id:
        """
        return str(legacy_id).translate(ESCAPE_TABLE)

    async def jid_username_to_legacy_id(self, jid_username: str) -> LegacyUserIdType:
        """
        Convert a JID user part to a legacy ID.

        Should be overridden in case legacy IDs are not strings, or more generally
        for any case where the username part of a JID (unescaped with to the mapping
        defined by :xep:`0106`) is not enough to identify a contact on the legacy network.

        Default implementation is an identity operation

        :param jid_username: User part of a JID, ie "user" in "user@example.com"
        :return: An identifier for the user on the legacy network.
        """
        return _unescape_node(jid_username)


def is_markable(stanza: Union[Message, Presence]):
    if isinstance(stanza, Presence):
        return False
    return bool(stanza["body"])


ESCAPE_TABLE = "".maketrans(
    {v: k for k, v in JID_UNESCAPE_TRANSFORMATIONS.items()}  # type:ignore
)

log = logging.getLogger(__name__)
