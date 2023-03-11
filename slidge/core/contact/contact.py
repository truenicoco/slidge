import logging
import warnings
from datetime import date, datetime
from typing import TYPE_CHECKING, Generic, Iterable, Optional, Union

from slixmpp import JID, Message, Presence
from slixmpp.exceptions import IqError

from ...util import SubclassableOnce
from ...util.types import AvatarType, LegacyMessageType, LegacyUserIdType
from ...util.xep_0292.stanza import VCard4
from .. import config
from ..mixins import FullCarbonMixin
from ..mixins.recipient import ReactionRecipientMixin, ThreadRecipientMixin

if TYPE_CHECKING:
    from ..session import BaseSession


class LegacyContact(
    Generic[LegacyUserIdType],
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

    mtype = "chat"
    _can_send_carbon = True
    is_group = False

    _ONLY_SEND_PRESENCE_CHANGES = True

    STRIP_SHORT_DELAY = True

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
        self.legacy_id = legacy_id
        self.jid_username = jid_username

        self.added_to_roster = False

        self._name: Optional[str] = None
        self._avatar: Optional[Union[AvatarType, bool]] = None

        self._subscribe_from = True
        self._subscribe_to = True

        if self.xmpp.MARK_ALL_MESSAGES:
            self._sent_order = list[str]()

        self.xmpp = session.xmpp
        self.jid = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        self.jid.resource = self.RESOURCE
        self.log = logging.getLogger(f"{self.user.bare_jid}:{self.jid.bare}")

    def __repr__(self):
        return f"<Contact '{self.legacy_id}'/'{self.jid.bare}'>"

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
            if (
                isinstance(stanza, Presence)
                and not self.added_to_roster
                and stanza["type"] != "subscribe"
            ):
                return
            if self.xmpp.MARK_ALL_MESSAGES and is_markable(stanza):
                self._sent_order.append(stanza["id"])
            stanza["to"] = self.user.jid
            stanza.send()

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
        if kwargs.get("carbon"):
            self.session.sent[
                legacy_msg_id
            ] = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id)
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
        """
        Set the avatar. self.set_avatar() should be preferred because you can provide
        a unique ID for the avatar, to help caching.
        """
        self.xmpp.loop.create_task(self.set_avatar(a))

    async def set_avatar(
        self,
        a: Optional[AvatarType],
        avatar_unique_id: Optional[Union[int, str]] = None,
        blocking=False,
    ):
        """
        Set the avatar for this contact

        :param a: Any avatar format supported by slidge
        :param avatar_unique_id: If possible, provide a unique ID to cache the avatar.
            If it is not provided, the SHA-1 of the avatar will be used,
            unless it is an HTTP url. In this case, the url will be used,
            along with etag or last modified HTTP headers, to avoid fetching
            uselessly. Beware of legacy plugin where URLs are not stable.
        :param blocking: if True, will await setting the avatar, if False, launch in a task
        :return:
        """
        awaitable = self.xmpp.pubsub.set_avatar(
            jid=self.jid.bare,
            avatar=a,
            unique_id=avatar_unique_id,
            restrict_to=self.user.jid.bare,
        )
        if blocking:
            await awaitable
        else:
            self.xmpp.loop.create_task(awaitable)
        # if it's bytes, we don't want to cache it in RAM, so just a bool to know it has been set
        self._avatar = isinstance(a, bytes) or a

    def get_avatar(self):
        if not self._avatar:
            return
        return self.xmpp.pubsub.get_avatar(jid=self.jid.bare)

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
            await self._set_roster(**kw)
        except PermissionError:
            warnings.warn(
                "Slidge does not have privileges to add contacts to the roster."
                "Refer to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html "
                "for more info."
            )
            if config.ROSTER_PUSH_PRESENCE_SUBSCRIPTION_REQUEST_FALLBACK:
                self._send_subscription_request()
            return
        except IqError as e:
            self.log.warning("Could not add to roster", exc_info=e)
        else:
            self.added_to_roster = True
            self._send_last_presence()

    async def _set_roster(self, **kw):
        try:
            return await self.xmpp["xep_0356"].set_roster(**kw)
        except PermissionError:
            return await self.xmpp["xep_0356_old"].set_roster(**kw)

    def _send_subscription_request(self):
        presence = self.xmpp.make_presence(
            pfrom=self.jid.bare,
            ptype="subscribe",
            pstatus=f"I'm already your friend on {self.xmpp.COMPONENT_TYPE}, but "
            f"slidge is not allowed to manage your roster.",
        )
        presence["nick"] = self.name
        # very awkward, slixmpp bug maybe?
        presence.append(presence["nick"])
        self._send(presence)

    def unsubscribe(self):
        """
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
        update the nickname, avatar, vcard [..] of this contact, by making
        "legacy API calls".
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
