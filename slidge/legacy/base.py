import hashlib
import logging
from abc import ABC
from datetime import datetime
from typing import Optional, Literal, Dict, Any, Hashable, List

from slixmpp import Message, JID, Iq, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from ..db import GatewayUser, user_store
from ..gateway import BaseGateway


class LegacyContact:
    """
    This class represents a contact a gateway user can interact with.
    If this is subclassed, make sure to change :py:attr:`.Roster.contact_cls` accordingly
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
    }
    """
    A list of features advertised through service discovery and client capabilities.
    """

    xmpp: BaseGateway = None

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

        self._name = None
        self._avatar = None

        self.xmpp.loop.create_task(self.make_caps())
        self.xmpp.loop.create_task(self.make_vcard())

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
    def name(self, n: str):
        self._name = n

    @property
    def avatar(self):
        """
        An image that represents this contact
        """
        return self._avatar

    @avatar.setter
    def avatar(self, a: bytes):
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
        vcard = self.xmpp["xep_0054"].make_vcard()
        if self.avatar is not None:
            vcard["PHOTO"]["BINVAL"] = self.avatar
            await self.xmpp["xep_0153"].api["set_hash"](
                jid=self.jid, args=hashlib.sha1(self.avatar).hexdigest()
            )
        await self.xmpp["xep_0054"].api["set_vcard"](
            jid=self.jid,
            args=vcard,
        )

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
        :func:`.LegacyContact.inactive` is probably more relevant.
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

    def chat_state(self, state: str):
        msg = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid)
        msg["chat_state"] = state
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

    def ack(self, msg: Message):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param msg: The message this marker refers to
        """
        self.send_marker(msg, "acknowledged")

    def received(self, msg: Message):
        """
        Send a "received" message marker (:xep:`0333`) from this contact to the user.

        :param msg: The message this marker refers to
        """
        self.send_marker(msg, "received")

    def displayed(self, msg: Message):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param msg: The message this marker refers to
        """
        self.send_marker(msg, "displayed")

    def send_marker(
        self, msg: Message, marker: Literal["acknowledged", "received", "displayed"]
    ):
        """
        Send a message marker (:xep:`0333`) from this contact to the user.

        :param msg: The message this marker refers to
        :param marker: The marker type
        """
        self.xmpp["xep_0333"].send_marker(
            mto=self.user.jid,
            id=msg["id"],
            marker=marker,
            mfrom=self.jid,
        )

    def send_message(
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
        :param legacy_msg_id:
        """
        msg = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, mbody=body)
        if chat_state is not None:
            msg["chat_state"] = chat_state
        msg.send()
        if legacy_msg_id is not None and self.session.store_unread_by_user:
            i = msg.get_id()
            log.debug("Storing correspondence between %s and %s", i)
            self.session.unread_by_user[i] = legacy_msg_id
        return msg

    def carbon(self, body: str, date: datetime):
        """
        Sync a message sent from an official client by the gateway user to XMPP.

        Uses xep:`0356` to impersonate the XMPP user and send a carbon message.

        :param str body: Body of the message.
        :param str date: When was this message sent.
        """
        # we use Message() directly because we need xmlns="jabber:client"
        log.debug("%s - %s", self.user.jid, self.jid)
        msg = Message()
        msg["from"] = self.user.jid.bare
        msg["to"] = self.jid.bare
        msg["type"] = "chat"
        msg["body"] = body
        msg["delay"].set_stamp(date)

        carbon = Message()
        carbon["from"] = self.user.jid
        carbon["to"] = self.user.jid
        carbon["type"] = "chat"
        carbon["carbon_sent"] = msg
        carbon.enable("no-copy")

        self.xmpp["xep_0356"].send_privileged_message(carbon)


class LegacyRoster:
    """
    Virtual roster of a gateway user, that allows to represent all
    of their contacts as singleton instances (if used properly and not too bugged).

    The point of having singletons is for slixmpp to correctly advertise
    capabilities and vcard of contacts.

    If overridden (see :class:`.signal.Roster`), make sure to update :py:attr:`.Session.roster_cls`
    accordingly.
    """

    contact_cls = LegacyContact

    def __init__(self, session: "BaseSession"):
        self.session = session
        self.contacts_by_bare_jid: Dict[str, LegacyContact] = {}
        self.contacts_by_legacy_id: Dict[Any, LegacyContact] = {}

    def by_jid(self, contact_jid: JID) -> LegacyContact:
        """
        Retrieve a contact by their JID

        If the contact was not instantiated before, it will be created
        using :meth:`.LegacyRoster.jid_username_to_legacy_id` to infer their
        legacy user ID.

        :param contact_jid:
        :return:
        """
        bare = contact_jid.bare
        c = self.contacts_by_bare_jid.get(bare)
        if c is None:
            jid_username = str(contact_jid.username)
            log.debug("Contact %s not found", contact_jid)
            c = self.contact_cls(
                self.session,
                self.jid_username_to_legacy_id(jid_username),
                jid_username,
            )
            c.bare_jid = bare
            self.contacts_by_bare_jid[bare] = c
        return c

    def by_legacy_id(self, legacy_id: Any) -> LegacyContact:
        """
        Retrieve a contact by their legacy_id

        If the contact was not instantiated before, it will be created
        using :meth:`.LegacyRoster.legacy_id_to_jid_username` to infer their
        legacy user ID.

        :param legacy_id:
        :return:
        """
        c = self.contacts_by_legacy_id.get(legacy_id)
        if c is None:
            log.debug("Contact %s not found in roster", legacy_id)
            c = self.contact_cls(
                self.session, legacy_id, self.legacy_id_to_jid_username(legacy_id)
            )
            self.contacts_by_legacy_id[legacy_id] = c
        return c

    def by_stanza(self, s) -> LegacyContact:
        """
        Retrieve a contact by the destination of a stanza

        See :meth:`.Roster.by_legacy_id` for more info.

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


class BaseSession(ABC):
    """
    Represents a gateway user logged in to the network and performing actions.

    Must be overridden for a functional slidge plugin
    """

    store_unacked = True
    """
    If the legacy network supports message receipts, keep track of messages for later
    sending back a receipt to the user.
    """
    store_unread = True
    """
    If the legacy network supports 'read marks', keep track of messages sent by the user
    to later mark them as read by their contacts
    """
    store_unread_by_user = True
    """
    If the legacy network supports 'read marks', keep track of messages received by the user
    and transmit read marks from XMPP to the legacy network
    """
    roster_cls = LegacyRoster
    """
    Roster class to use for this session. Change it if you override :class:`.Roster`
    """
    xmpp: Optional[BaseGateway] = None

    def __init__(self, xmpp: BaseGateway, user: GatewayUser):
        self.xmpp = xmpp
        self.user = user
        if self.store_unacked:
            self.unacked: Dict[Any, Message] = {}
        if self.store_unread:
            self.unread: Dict[Any, Message] = {}
        if self.store_unread_by_user:
            self.unread_by_user: Dict[str, Any] = {}
        self.logged = False

        self.contacts = self.roster_cls(self)
        self.post_init()

    def post_init(self):
        """
        Add useful attributes for your session here, if necessary
        """
        pass

    @classmethod
    def from_stanza(cls, s) -> "BaseSession":
        """
        Get a user's :class:`LegacySession` using the "from" field of a stanza

        Ensure that we only have a single session instance per user

        :param s:
        :return:
        """
        user = user_store.get_by_stanza(s)
        if user is None:
            raise KeyError(s.get_from())
        session = sessions.get(user)
        if session is None:
            sessions[user] = session = cls(cls.xmpp, user)
        return session

    async def login(self, p: Presence):
        """
        Login the gateway user to the legacy network.

        Triggered when the gateway receives an online presence from the user, so the legacy client
        should keep a list of logged-in users to avoid useless calls to the login process.

        :param p:
        """
        raise NotImplementedError

    async def logout(self, p: Presence):
        """
        Logout the gateway user from the legacy network.

        Called when the gateway receives an offline presence from the user.
        Just override this and ``pass`` to implement a bouncer-like ("always connected") functionality.

        :param p:
        """
        raise NotImplementedError

    async def send_from_msg(self, m: Message):
        legacy_msg_id = await self.send(m, self.contacts.by_stanza(m))
        if self.store_unacked:
            self.unacked[legacy_msg_id] = m
        if self.store_unread:
            self.unread[legacy_msg_id] = m

    async def active_from_msg(self, m: Message):
        await self.active(self.contacts.by_stanza(m))

    async def inactive_from_msg(self, m: Message):
        await self.inactive(self.contacts.by_stanza(m))

    async def composing_from_msg(self, m: Message):
        await self.composing(self.contacts.by_stanza(m))

    async def displayed_from_msg(self, m: Message):
        displayed_msg_id = m["displayed"]["id"]
        try:
            legacy_msg_id = self.unread_by_user.pop(displayed_msg_id)
        except KeyError:
            log.debug(
                "Received read marker for a msg we did not send: %s",
                self.unread_by_user,
            )
        else:
            await self.displayed(legacy_msg_id, self.contacts.by_stanza(m))

    async def send(self, m: Message, c: LegacyContact) -> Optional[Hashable]:
        """
        The user sends a message from xmpp to the legacy network

        :param m: The XMPP message
        :param c: Recipient of the message
        :return: An ID of some sort that can be used later to ack and mark the message
            as read by the user
        """
        raise NotImplementedError

    async def active(self, c: LegacyContact):
        """
        The use sens an 'active' chat state to the legacy network

        :param c: Recipient of the active chat state
        """
        raise NotImplementedError

    async def inactive(self, c: LegacyContact):
        """
        The use sens an 'inactive' chat state to the legacy network

        :param c:
        :return:
        """
        raise NotImplementedError

    async def composing(self, c: LegacyContact):
        """
        The use sens an 'inactive' starts typing

        :param c:
        :return:
        """
        raise NotImplementedError

    async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
        """


        :param legacy_msg_id: Identifier of the message, return value of by :meth:`.BaseSession.send`
        :param c:
        :return:
        """
        raise NotImplementedError


class BaseLegacyClient(ABC):
    """
    Abstract base class for interacting with the legacy network
    """

    session_cls = BaseSession
    """
    This is automatically overridden by the legacy network subclass of :class:`.BaseSession`
    """

    def __init__(self, xmpp: BaseGateway):
        """
        :param xmpp: The gateway, to interact with the XMPP network
        """
        self.xmpp = LegacyContact.xmpp = self.session_cls.xmpp = xmpp

        xmpp["xep_0077"].api.register(self._user_validate, "user_validate")
        xmpp.add_event_handler("user_unregister", self._on_user_unregister)

        get_session = self.session_cls.from_stanza

        # fmt: off
        async def logout(p): await get_session(p).logout(p)
        async def msg(m): await get_session(m).send_from_msg(m)
        async def disp(m): await get_session(m).displayed_from_msg(m)
        async def active(m): await get_session(m).active_from_msg(m)
        async def inactive(m): await get_session(m).inactive_from_msg(m)
        async def composing(m): await get_session(m).composing_from_msg(m)
        # fmt: on

        xmpp.add_event_handler("legacy_login", self.legacy_login)
        xmpp.add_event_handler("legacy_logout", logout)
        xmpp.add_event_handler("legacy_message", msg)
        self.xmpp.add_event_handler("marker_displayed", disp)
        self.xmpp.add_event_handler("chatstate_active", active)
        self.xmpp.add_event_handler("chatstate_inactive", inactive)
        self.xmpp.add_event_handler("chatstate_composing", composing)

    def config(self, argv: List[str]):
        """
        Override this to access CLI args to configure the slidge plugin

        :param argv: CLI args that were not parsed by Slidge
        """
        pass

    async def legacy_login(self, p: Presence):
        """
        Logs a :class:`.BaseSession` instance to the legacy network

        :param p: Presence from a :class:`.GatewayUser` directed at the gateway's own JID
        """
        session = self.session_cls.from_stanza(p)
        if not session.logged:
            await session.login(p)

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        log.debug("User validate: %s", (ifrom.bare, iq))
        form = iq["register"]["form"].get_values()

        for field in self.xmpp.REGISTRATION_FIELDS:
            if field.required and not form.get(field.name):
                raise XMPPError("Please fill in all fields", etype="modify")

        form_dict = {f.name: form.get(f.name) for f in self.xmpp.REGISTRATION_FIELDS}

        try:
            await self.validate(ifrom, form_dict)
        except LegacyError as e:
            raise ValueError(f"Login Problem: {e}")
        else:
            user_store.add(ifrom, form)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited in terms of validation, it is OK to validate
        anything that looks good here and continue the legacy auth process via direct messages
        to the user (using :func:`.BaseGateway.input` for instance)

        :param user_jid:
        :param registration_form:
        """
        raise NotImplementedError

    async def _on_user_unregister(self, iq: Iq):
        await self.unregister(user_store.get_by_stanza(iq), iq)

    async def unregister(self, user: GatewayUser, iq: Iq):
        """
        Called when the user unregister from the gateway

        :param user:
        :param iq:
        """
        raise NotImplementedError


log = logging.getLogger(__name__)
sessions: Dict[GatewayUser, BaseSession] = {}
