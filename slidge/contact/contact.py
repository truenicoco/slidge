import datetime
import logging
import warnings
from datetime import date
from typing import TYPE_CHECKING, Generic, Iterable, Optional, Union

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import IqError
from slixmpp.plugins.xep_0292.stanza import VCard4
from slixmpp.types import MessageTypes

from ..core import config
from ..core.mixins import FullCarbonMixin
from ..core.mixins.avatar import AvatarMixin
from ..core.mixins.disco import ContactAccountDiscoMixin
from ..core.mixins.recipient import ReactionRecipientMixin, ThreadRecipientMixin
from ..util import SubclassableOnce
from ..util.types import LegacyUserIdType, MessageOrPresenceTypeVar

if TYPE_CHECKING:
    from ..core.session import BaseSession
    from ..group.participant import LegacyParticipant


class LegacyContact(
    Generic[LegacyUserIdType],
    AvatarMixin,
    ContactAccountDiscoMixin,
    FullCarbonMixin,
    ReactionRecipientMixin,
    ThreadRecipientMixin,
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

    _avatar_pubsub_broadcast = True
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
        self.user = session.user
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

        if self.xmpp.MARK_ALL_MESSAGES:
            self._sent_order = list[str]()

        self.xmpp = session.xmpp
        self.jid = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        self.jid.resource = self.RESOURCE
        self.log = logging.getLogger(f"{self.user.bare_jid}:{self.jid.bare}")
        self.participants = set["LegacyParticipant"]()
        self.is_friend: bool = False
        self.__added_to_roster = False

    def __repr__(self):
        return f"<Contact '{self.legacy_id}'/'{self.jid.bare}'>"

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
            stanza["from"] = self.user.jid
            self._privileged_send(stanza)
            return stanza  # type:ignore

        if isinstance(stanza, Presence):
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
        if self.xmpp.MARK_ALL_MESSAGES and is_markable(stanza):
            self._sent_order.append(stanza["id"])
        stanza["to"] = self.user.jid
        stanza.send()
        return stanza

    def get_msg_xmpp_id_up_to(self, horizon_xmpp_id: str):
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
        for i, xmpp_id in enumerate(self._sent_order):
            if xmpp_id == horizon_xmpp_id:
                break
        else:
            return
        i += 1
        res = self._sent_order[:i]
        self._sent_order = self._sent_order[i:]
        return res

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
        for p in self.participants:
            p.nickname = n
        self._name = n
        self.xmpp.pubsub.set_nick(user=self.user, jid=self.jid.bare, nick=n)

    def _post_avatar_update(self):
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

        self.xmpp.vcard.set_vcard(self.jid.bare, vcard, {self.user.jid.bare})

    async def add_to_roster(self, force=False):
        """
        Add this contact to the user roster using :xep:`0356`

        :param force: add even if the contact was already added successfully
        """
        if self.__added_to_roster and not force:
            return
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
            await self._set_roster(**kw)
        except PermissionError:
            warnings.warn(
                "Slidge does not have privileges to add contacts to the roster. Refer"
                " to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html for"
                " more info."
            )
            if config.ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK:
                self.send_friend_request(
                    f"I'm already your friend on {self.xmpp.COMPONENT_TYPE}, but "
                    "slidge is not allowed to manage your roster."
                )
            return
        except IqError as e:
            self.log.warning("Could not add to roster", exc_info=e)
        else:
            # we only broadcast pubsub events for contacts added to the roster
            # so if something was set before, we need to push it now
            self.__added_to_roster = True
            self.session.create_task(self.__broadcast_pubsub_items())
            self.send_last_presence()

    async def __broadcast_pubsub_items(self):
        await self.xmpp.pubsub.broadcast_all(JID(self.jid.bare), self.user.jid)

    async def _set_roster(self, **kw):
        try:
            return await self.xmpp["xep_0356"].set_roster(**kw)
        except PermissionError:
            return await self.xmpp["xep_0356_old"].set_roster(**kw)

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
            self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, ptype=ptype)  # type: ignore

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


def is_markable(stanza: Union[Message, Presence]):
    if isinstance(stanza, Presence):
        return False
    return bool(stanza["body"])


log = logging.getLogger(__name__)
