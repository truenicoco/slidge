import asyncio
import logging
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Hashable,
    Literal,
    Optional,
    IO,
    Dict,
    Any,
    TYPE_CHECKING,
    Type,
    Generic,
    TypeVar,
    Union,
)

from slixmpp import JID, Iq, Message

from ..types import AvatarType
from ..util import SubclassableOnce

if TYPE_CHECKING:
    from .session import BaseSession


class LegacyContact(metaclass=SubclassableOnce):
    """
    This class represents a contact a gateway user can interact with.
    """

    RESOURCE: str = "slidge"
    """
    A resource is required for chat states (and maybe other stuff) to work properly.
    This is the name of the resource the contacts will use.
    """
    FEATURES = {
        "http://jabber.org/protocol/chatstates",
        "urn:xmpp:receipts",
        "vcard-temp",
        "jabber:x:oob",
        "urn:xmpp:message-correct:0",
    }
    """
    A list of features advertised through service discovery and client capabilities.
    """

    def __init__(
        self,
        session: "BaseSession",
        legacy_id: Hashable,
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

        self._name: Optional[str] = None
        self._avatar: Optional[AvatarType] = None

        self.xmpp = session.xmpp
        asyncio.create_task(self.make_caps())
        asyncio.create_task(self.make_vcard())

    def __repr__(self):
        return f"<LegacyContact '{self.jid}' - '{self.name}' - {self.user}>"

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
        self.xmpp.loop.create_task(self.make_vcard())

    async def make_caps(self):
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

    async def make_vcard(self):
        """
        Configure slixmpp to correctly set this contact's vcard (in fact only its avatar ATM)
        """
        await self.xmpp.make_vcard(jid=self.jid, avatar=self.avatar)

    async def add_to_roster(self):
        """
        Add a contact to a user roster using :xep:`0356`
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

    def online(self):
        """
        Send an "online" presence from this contact to the user.
        """
        self.xmpp.send_presence(
            pfrom=self.jid,
            pto=self.user.jid.bare,
        )

    def away(self):
        """
        Send an "away" presence from this contact to the user.

        Does not make much sense in the context of mobile, "always connected" network where
        :func:`slidge.LegacyContact.inactive` is probably more relevant.
        """
        self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, pshow="away")

    def busy(self):
        """
        Send a "busy" presence from this contact to the user.
        """
        self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, pshow="busy")

    def status(self, text: str):
        """
        Set a contact's status
        """
        self.xmpp.send_presence(pfrom=self.jid, pto=self.user.jid.bare, pstatus=text)

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

    def chat_state(self, state: str):
        msg = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, mtype="chat")
        msg["chat_state"] = state
        msg.enable("no-store")
        msg.send()

    def active(self):
        """
        Send an "active" chat state (:xep:`0085`) from this contact to the user.
        """
        self.chat_state("active")

    def composing(self):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self.chat_state("composing")

    def paused(self):
        """
        Send a "paused" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self.chat_state("paused")

    def inactive(self):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        log.debug("%s go inactive", self)
        self.chat_state("inactive")

    def ack(self, legacy_msg_id: Hashable):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self.send_marker(legacy_msg_id, "acknowledged")

    def received(self, legacy_msg_id: Hashable):
        """
        Send a "received" message marker (:xep:`0333`) and a "message delivery receipt"
        (:xep:`0184`)
        from this contact to the user

        :param legacy_msg_id: The message this marker refers to
        """
        self.send_marker(legacy_msg_id, "received")

    def displayed(self, legacy_msg_id: Hashable):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self.send_marker(legacy_msg_id, "displayed")

    def send_marker(
        self,
        legacy_msg_id: Hashable,
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

    def _make_message(self, **kwargs):
        return self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, **kwargs)

    def _send_message(self, msg: Message, legacy_msg_id: Optional[Any] = None):
        if legacy_msg_id is not None:
            msg.set_id(self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id))
        msg.send()

    def send_text(
        self,
        body: str = "",
        chat_state: Optional[str] = "active",
        legacy_msg_id: Optional[Hashable] = None,
    ):
        """
        Transmit a message from the contact to the user

        :param body: Context of the message
        :param chat_state: By default, will send an "active" chat state (:xep:`0085`) along with the
            message. Set this to ``None`` if this is not desired.
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        """
        msg = self._make_message(mbody=body, mtype="chat")
        if chat_state is not None:
            msg["chat_state"] = chat_state
        self._send_message(msg, legacy_msg_id)
        return msg

    async def send_file(
        self,
        filename: Union[Path, str],
        content_type: Optional[str] = None,
        input_file: Optional[IO[bytes]] = None,
    ):
        try:
            log.debug("HOST: %s", self.xmpp.server_host)
            url = await self.xmpp["xep_0363"].upload_file(
                filename=filename,
                content_type=content_type,
                input_file=input_file,
            )
        except Exception as e:
            log.exception(e)
        else:
            msg = self._make_message()
            msg["oob"]["url"] = url
            msg["body"] = url
            self._send_message(msg)

    def _carbon(self, msg: Message):
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
        Sync a message sent from an official client by the gateway user to XMPP.

        Uses xep:`0356` to impersonate the XMPP user and send a carbon message.

        :param str body: Body of the message.
        :param legacy_id:
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

        self._carbon(msg)
        return msg.get_id()

    def carbon_read(self, legacy_msg_id: Any, date: Optional[datetime] = None):
        """
        Uses xep:`0356` to impersonate the XMPP user and send a carbon message.

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

        self._carbon(msg)

    def correct(self, legacy_msg_id: Any, new_text: str):
        msg = self._make_message()
        msg["replace"]["id"] = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id)
        msg["body"] = new_text
        self._send_message(msg)


LegacyContactType = TypeVar("LegacyContactType", bound=LegacyContact)


class LegacyRoster(Generic[LegacyContactType], metaclass=SubclassableOnce):
    """
    Virtual roster of a gateway user, that allows to represent all
    of their contacts as singleton instances (if used properly and not too bugged).

    The point of having singletons is for slixmpp to correctly advertise
    capabilities and vcard of contacts.
    """

    def __init__(self, session: "BaseSession"):
        self._contact_cls: Type[
            LegacyContactType
        ] = LegacyContact.get_self_or_unique_subclass()
        self._contact_cls.xmpp = session.xmpp

        self.session = session
        self.contacts_by_bare_jid: Dict[str, LegacyContactType] = {}
        self.contacts_by_legacy_id: Dict[Any, LegacyContactType] = {}

    def __iter__(self):
        return iter(self.contacts_by_legacy_id.values())

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
        c = self.contacts_by_bare_jid.get(bare)
        if c is None:
            jid_username = str(contact_jid.username)
            log.debug("Contact %s not found", contact_jid)
            c = self._contact_cls(
                self.session,
                self.jid_username_to_legacy_id(jid_username),
                jid_username,
            )
            self.contacts_by_bare_jid[bare] = c
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
        c = self.contacts_by_legacy_id.get(legacy_id)
        if c is None:
            log.debug("Contact %s not found in roster", legacy_id)
            c = self._contact_cls(
                self.session, legacy_id, self.legacy_id_to_jid_username(legacy_id)
            )
            self.contacts_by_legacy_id[legacy_id] = c
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
    def jid_username_to_legacy_id(jid_username: str) -> Hashable:
        """
        Convert a JID user part to a legacy ID.

        Should be overridden in case legacy IDs are not strings, for instance

        :param jid_username:
        :return:
        """
        return jid_username


LegacyRosterType = TypeVar("LegacyRosterType", bound=LegacyRoster)

log = logging.getLogger(__name__)
