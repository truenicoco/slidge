import datetime
import logging
import warnings
from datetime import date
from typing import TYPE_CHECKING, Generic, Iterable, Optional, Self, Union
from xml.etree import ElementTree as ET

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.plugins.xep_0292.stanza import VCard4
from slixmpp.types import MessageTypes

from ..core import config
from ..core.mixins import AvatarMixin, FullCarbonMixin, StoredAttributeMixin
from ..core.mixins.db import UpdateInfoMixin
from ..core.mixins.disco import ContactAccountDiscoMixin
from ..core.mixins.recipient import ReactionRecipientMixin, ThreadRecipientMixin
from ..db.models import Contact
from ..util import SubclassableOnce
from ..util.types import ClientType, LegacyUserIdType, MessageOrPresenceTypeVar

if TYPE_CHECKING:
    from ..core.session import BaseSession
    from ..group.participant import LegacyParticipant


class LegacyContact(
    Generic[LegacyUserIdType],
    StoredAttributeMixin,
    AvatarMixin,
    ContactAccountDiscoMixin,
    FullCarbonMixin,
    ReactionRecipientMixin,
    ThreadRecipientMixin,
    UpdateInfoMixin,
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

    session: "BaseSession"

    RESOURCE: str = "slidge"
    """
    A full JID, including a resource part is required for chat states (and maybe other stuff)
    to work properly. This is the name of the resource the contacts will use.
    """
    PROPAGATE_PRESENCE_TO_GROUPS = True

    mtype: MessageTypes = "chat"
    _can_send_carbon = True
    is_group = False

    _ONLY_SEND_PRESENCE_CHANGES = True

    STRIP_SHORT_DELAY = True
    _NON_FRIEND_PRESENCES_FILTER = {"subscribe", "unsubscribed"}

    _avatar_bare_jid = True

    INVITATION_RECIPIENT = True

    def __init__(
        self,
        session: "BaseSession",
        legacy_id: LegacyUserIdType,
        jid_username: str,
    ):
        """
        :param session: The session this contact is part of
        :param legacy_id: The contact's legacy ID
        :param jid_username: User part of this contact's 'puppet' JID.
            NB: case-insensitive, and some special characters are not allowed
        """
        super().__init__()
        self.session = session
        self.legacy_id: LegacyUserIdType = legacy_id
        """
        The legacy identifier of the :term:`Legacy Contact`.
        By default, this is the :term:`JID Local Part` of this
        :term:`XMPP Entity`.

        Controlling what values are valid and how they are translated from a
        :term:`JID Local Part` is done in :meth:`.jid_username_to_legacy_id`.
        Reciprocally, in :meth:`legacy_id_to_jid_username` the inverse
        transformation is defined.
        """
        self.jid_username = jid_username

        self._name: Optional[str] = None

        self.xmpp = session.xmpp
        self.jid = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        self.jid.resource = self.RESOURCE
        self.log = logging.getLogger(self.jid.bare)
        self._set_logger_name()
        self._is_friend: bool = False
        self._added_to_roster = False
        self._caps_ver: str | None = None
        self._vcard_fetched = False
        self._vcard: str | None = None
        self._client_type: ClientType = "pc"

    async def get_vcard(self, fetch=True) -> VCard4 | None:
        if fetch and not self._vcard_fetched:
            await self.fetch_vcard()
        if self._vcard is None:
            return None

        return VCard4(xml=ET.fromstring(self._vcard))

    @property
    def is_friend(self):
        return self._is_friend

    @is_friend.setter
    def is_friend(self, value: bool):
        if value == self._is_friend:
            return
        self._is_friend = value
        if self._updating_info:
            return
        self.__ensure_pk()
        assert self.contact_pk is not None
        self.xmpp.store.contacts.set_friend(self.contact_pk, value)

    @property
    def added_to_roster(self):
        return self._added_to_roster

    @added_to_roster.setter
    def added_to_roster(self, value: bool):
        if value == self._added_to_roster:
            return
        self._added_to_roster = value
        if self._updating_info:
            return
        if self.contact_pk is None:
            # during LegacyRoster.fill()
            return
        self.xmpp.store.contacts.set_added_to_roster(self.contact_pk, value)

    @property
    def participants(self) -> list["LegacyParticipant"]:
        if self.contact_pk is None:
            return []

        self.__ensure_pk()
        from ..group.participant import LegacyParticipant

        return [
            LegacyParticipant.get_self_or_unique_subclass().from_store(
                self.session, stored, contact=self
            )
            for stored in self.xmpp.store.participants.get_for_contact(self.contact_pk)
        ]

    @property
    def user_jid(self):
        return self.session.user_jid

    @property  # type:ignore
    def DISCO_TYPE(self) -> ClientType:
        return self._client_type

    @DISCO_TYPE.setter
    def DISCO_TYPE(self, value: ClientType) -> None:
        self.client_type = value

    @property
    def client_type(self) -> ClientType:
        """
        The client type of this contact, cf https://xmpp.org/registrar/disco-categories.html#client

        Default is "pc".
        """
        return self._client_type

    @client_type.setter
    def client_type(self, value: ClientType) -> None:
        self._client_type = value
        if self._updating_info:
            return
        self.__ensure_pk()
        assert self.contact_pk is not None
        self.xmpp.store.contacts.set_client_type(self.contact_pk, value)

    def _set_logger_name(self):
        self.log.name = f"{self.user_jid.bare}:contact:{self}"

    def __repr__(self):
        return f"<Contact #{self.contact_pk} '{self.name}' ({self.legacy_id} - {self.jid.local})'>"

    def __ensure_pk(self):
        if self.contact_pk is not None:
            return
        # This happens for legacy modules that don't follow the Roster.fill /
        # populate contact attributes in Contact.update_info() method.
        # This results in (even) less optimised SQL writes and read, but
        # we allow it because it fits some legacy network libs better.
        with self.xmpp.store.session() as orm:
            orm.commit()
            stored = self.xmpp.store.contacts.get_by_legacy_id(
                self.user_pk, str(self.legacy_id)
            )
            if stored is None:
                self.contact_pk = self.xmpp.store.contacts.update(self, commit=True)
            else:
                self.contact_pk = stored.id
        assert self.contact_pk is not None

    def __get_subscription_string(self):
        if self.is_friend:
            return "both"
        return "none"

    def __propagate_to_participants(self, stanza: Presence):
        if not self.PROPAGATE_PRESENCE_TO_GROUPS:
            return

        ptype = stanza["type"]
        if ptype in ("available", "chat"):
            func_name = "online"
        elif ptype in ("xa", "unavailable"):
            # we map unavailable to extended_away, because offline is
            # "participant leaves the MUC"
            # TODO: improve this with a clear distinction between participant
            #       and member list
            func_name = "extended_away"
        elif ptype == "busy":
            func_name = "busy"
        elif ptype == "away":
            func_name = "away"
        else:
            return

        last_seen: Optional[datetime.datetime] = (
            stanza["idle"]["since"] if stanza.get_plugin("idle", check=True) else None
        )

        kw = dict(status=stanza["status"], last_seen=last_seen)

        for part in self.participants:
            func = getattr(part, func_name)
            func(**kw)

    def _send(
        self, stanza: MessageOrPresenceTypeVar, carbon=False, nick=False, **send_kwargs
    ) -> MessageOrPresenceTypeVar:
        if carbon and isinstance(stanza, Message):
            stanza["to"] = self.jid.bare
            stanza["from"] = self.user_jid
            self._privileged_send(stanza)
            return stanza  # type:ignore

        if isinstance(stanza, Presence):
            if not self._updating_info:
                self.__propagate_to_participants(stanza)
            if (
                not self.is_friend
                and stanza["type"] not in self._NON_FRIEND_PRESENCES_FILTER
            ):
                return stanza  # type:ignore
        if self.name and (nick or not self.is_friend):
            n = self.xmpp.plugin["xep_0172"].stanza.UserNick()
            n["nick"] = self.name
            stanza.append(n)
        if (
            not self._updating_info
            and self.xmpp.MARK_ALL_MESSAGES
            and is_markable(stanza)
        ):
            self.__ensure_pk()
            assert self.contact_pk is not None
            self.xmpp.store.contacts.add_to_sent(self.contact_pk, stanza["id"])
        stanza["to"] = self.user_jid
        stanza.send()
        return stanza

    def get_msg_xmpp_id_up_to(self, horizon_xmpp_id: str) -> list[str]:
        """
        Return XMPP msg ids sent by this contact up to a given XMPP msg id.

        Plugins have no reason to use this, but it is used by slidge core
        for legacy networks that need to mark all messages as read (most XMPP
        clients only send a read marker for the latest message).

        This has side effects, if the horizon XMPP id is found, messages up to
        this horizon are not cleared, to avoid sending the same read mark twice.

        :param horizon_xmpp_id: The latest message
        :return: A list of XMPP ids or None if horizon_xmpp_id was not found
        """
        self.__ensure_pk()
        assert self.contact_pk is not None
        return self.xmpp.store.contacts.pop_sent_up_to(self.contact_pk, horizon_xmpp_id)

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
        self._set_logger_name()
        if self.is_friend and self.added_to_roster:
            self.xmpp.pubsub.broadcast_nick(
                user_jid=self.user_jid, jid=self.jid.bare, nick=n
            )
        if self._updating_info:
            # means we're in update_info(), so no participants, and no need
            # to write to DB now, it will be called in Roster.__finish_init_contact
            return
        for p in self.participants:
            p.nickname = n
        self.__ensure_pk()
        assert self.contact_pk is not None
        self.xmpp.store.contacts.update_nick(self.contact_pk, n)

    def _get_cached_avatar_id(self) -> Optional[str]:
        if self.contact_pk is None:
            return None
        return self.xmpp.store.contacts.get_avatar_legacy_id(self.contact_pk)

    def _post_avatar_update(self):
        self.__ensure_pk()
        assert self.contact_pk is not None
        self.xmpp.store.contacts.set_avatar(
            self.contact_pk,
            self._avatar_pk,
            None if self.avatar_id is None else str(self.avatar_id),
        )
        for p in self.participants:
            self.log.debug("Propagating new avatar to %s", p.muc)
            p.send_last_presence(force=True, no_cache_online=True)

    def set_vcard(
        self,
        /,
        full_name: Optional[str] = None,
        given: Optional[str] = None,
        surname: Optional[str] = None,
        birthday: Optional[date] = None,
        phone: Optional[str] = None,
        phones: Iterable[str] = (),
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
        for p in phones:
            vcard.add_tel(p)
        if country and locality:
            vcard.add_address(country, locality)
        elif country:
            vcard.add_address(country, locality)

        self._vcard = str(vcard)
        self._vcard_fetched = True
        self.session.create_task(
            self.xmpp.pubsub.broadcast_vcard_event(self.jid, self.user_jid, vcard)
        )

        if self._updating_info:
            return

        assert self.contact_pk is not None
        self.xmpp.store.contacts.set_vcard(self.contact_pk, self._vcard)

    def get_roster_item(self):
        item = {
            "subscription": self.__get_subscription_string(),
            "groups": [self.xmpp.ROSTER_GROUP],
        }
        if (n := self.name) is not None:
            item["name"] = n
        return {self.jid.bare: item}

    async def add_to_roster(self, force=False):
        """
        Add this contact to the user roster using :xep:`0356`

        :param force: add even if the contact was already added successfully
        """
        if self.added_to_roster and not force:
            return
        if config.NO_ROSTER_PUSH:
            log.debug("Roster push request by plugin ignored (--no-roster-push)")
            return
        try:
            await self._set_roster(
                jid=self.user_jid, roster_items=self.get_roster_item()
            )
        except PermissionError:
            warnings.warn(
                "Slidge does not have privileges to add contacts to the roster. Refer"
                " to https://slidge.im/core/admin/privilege.html for"
                " more info."
            )
            if config.ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK:
                self.send_friend_request(
                    f"I'm already your friend on {self.xmpp.COMPONENT_TYPE}, but "
                    "slidge is not allowed to manage your roster."
                )
            return
        except (IqError, IqTimeout) as e:
            self.log.warning("Could not add to roster", exc_info=e)
        else:
            # we only broadcast pubsub events for contacts added to the roster
            # so if something was set before, we need to push it now
            self.added_to_roster = True
            self.send_last_presence()

    async def __broadcast_pubsub_items(self):
        if not self.is_friend:
            return
        if not self.added_to_roster:
            return
        cached_avatar = self.get_cached_avatar()
        if cached_avatar is not None:
            await self.xmpp.pubsub.broadcast_avatar(
                self.jid.bare, self.session.user_jid, cached_avatar
            )
        nick = self.name

        if nick is not None:
            self.xmpp.pubsub.broadcast_nick(
                self.session.user_jid,
                self.jid.bare,
                nick,
            )

    async def _set_roster(self, **kw):
        try:
            await self.xmpp["xep_0356"].set_roster(**kw)
        except PermissionError:
            await self.xmpp["xep_0356_old"].set_roster(**kw)

    def send_friend_request(self, text: Optional[str] = None):
        presence = self._make_presence(ptype="subscribe", pstatus=text, bare=True)
        self._send(presence, nick=True)

    async def accept_friend_request(self, text: Optional[str] = None):
        """
        Call this to signify that this Contact has accepted to be a friend
        of the user.

        :param text: Optional message from the friend to the user
        """
        self.is_friend = True
        self.added_to_roster = True
        self.__ensure_pk()
        self.log.debug("Accepting friend request")
        presence = self._make_presence(ptype="subscribed", pstatus=text, bare=True)
        self._send(presence, nick=True)
        self.send_last_presence()
        await self.__broadcast_pubsub_items()
        self.log.debug("Accepted friend request")

    def reject_friend_request(self, text: Optional[str] = None):
        """
        Call this to signify that this Contact has refused to be a contact
        of the user (or that they don't want to be friends anymore)

        :param text: Optional message from the non-friend to the user
        """
        presence = self._make_presence(ptype="unsubscribed", pstatus=text, bare=True)
        self.offline()
        self._send(presence, nick=True)
        self.is_friend = False

    async def on_friend_request(self, text=""):
        """
        Called when receiving a "subscribe" presence, ie, "I would like to add
        you to my contacts/friends", from the user to this contact.

        In XMPP terms: "I would like to receive your presence updates"

        This is only called if self.is_friend = False. If self.is_friend = True,
        slidge will automatically "accept the friend request", ie, reply with
        a "subscribed" presence.

        When called, a 'friend request event' should be sent to the legacy
        service, and when the contact responds, you should either call
        self.accept_subscription() or self.reject_subscription()
        """
        pass

    async def on_friend_delete(self, text=""):
        """
        Called when receiving an "unsubscribed" presence, ie, "I would like to
        remove you to my contacts/friends" or "I refuse your friend request"
        from the user to this contact.

        In XMPP terms: "You won't receive my presence updates anymore (or you
        never have)".
        """
        pass

    async def on_friend_accept(self):
        """
        Called when receiving a "subscribed"  presence, ie, "I accept to be
        your/confirm that you are my friend" from the user to this contact.

        In XMPP terms: "You will receive my presence updates".
        """
        pass

    def unsubscribe(self):
        """
        (internal use by slidge)

        Send an "unsubscribe", "unsubscribed", "unavailable" presence sequence
        from this contact to the user, ie, "this contact has removed you from
        their 'friends'".
        """
        for ptype in "unsubscribe", "unsubscribed", "unavailable":
            self.xmpp.send_presence(pfrom=self.jid, pto=self.user_jid.bare, ptype=ptype)  # type: ignore

    async def update_info(self):
        """
        Fetch information about this contact from the legacy network

        This is awaited on Contact instantiation, and should be overridden to
        update the nickname, avatar, vcard [...] of this contact, by making
        "legacy API calls".

        To take advantage of the slidge avatar cache, you can check the .avatar
        property to retrieve the "legacy file ID" of the cached avatar. If there
        is no change, you should not call
        :py:meth:`slidge.core.mixins.avatar.AvatarMixin.set_avatar` or attempt
        to modify the ``.avatar`` property.
        """
        pass

    async def fetch_vcard(self):
        """
        It the legacy network doesn't like that you fetch too many profiles on startup,
        it's also possible to fetch it here, which will be called when XMPP clients
        of the user request the vcard, if it hasn't been fetched before
        :return:
        """
        pass

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime.datetime] = None,
        status_codes: Optional[set[int]] = None,
        user_full_jid: Optional[JID] = None,
        **presence_kwargs,
    ):
        p = super()._make_presence(last_seen=last_seen, **presence_kwargs)
        caps = self.xmpp.plugin["xep_0115"]
        if p.get_from().resource and self._caps_ver:
            p["caps"]["node"] = caps.caps_node
            p["caps"]["hash"] = caps.hash
            p["caps"]["ver"] = self._caps_ver
        return p

    @classmethod
    def from_store(cls, session, stored: Contact, *args, **kwargs) -> Self:
        contact = cls(
            session,
            cls.xmpp.LEGACY_CONTACT_ID_TYPE(stored.legacy_id),
            stored.jid.username,  # type: ignore
            *args,  # type: ignore
            **kwargs,  # type: ignore
        )
        contact.contact_pk = stored.id
        contact._name = stored.nick
        contact._is_friend = stored.is_friend
        contact._added_to_roster = stored.added_to_roster
        if (data := stored.extra_attributes) is not None:
            contact.deserialize_extra_attributes(data)
        contact._caps_ver = stored.caps_ver
        contact._set_logger_name()
        contact._AvatarMixin__avatar_unique_id = (  # type:ignore
            None
            if stored.avatar_legacy_id is None
            else session.xmpp.AVATAR_ID_TYPE(stored.avatar_legacy_id)
        )
        contact._avatar_pk = stored.avatar_id
        contact._vcard = stored.vcard
        contact._vcard_fetched = stored.vcard_fetched
        contact._client_type = stored.client_type
        return contact


def is_markable(stanza: Union[Message, Presence]):
    if isinstance(stanza, Presence):
        return False
    return bool(stanza["body"])


log = logging.getLogger(__name__)
