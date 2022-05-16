import hashlib
import logging
from abc import ABC
from datetime import datetime
from typing import Optional, Literal, Dict, Any

from slixmpp import Message, JID, Iq, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from ..db import GatewayUser, user_store
from ..gateway import BaseGateway


class LegacyContact(ABC):
    """
    This class represents a contact a gateway user can interact with
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
        user: GatewayUser,
        legacy_id: str,
        name: str = "",
        avatar: Optional[bytes] = None,
        extra_info: Optional[Any] = None
    ):
        """

        :param user: The user this contact can chat with
        :param legacy_id: The ID of the
        :param name: Name used for the user's roster entry
        :param avatar: An image representing the contact
        """
        self.user = user
        self.legacy_id = legacy_id
        self.name = name
        self.avatar = avatar
        self.xmpp.loop.create_task(self.make_vcard())
        self.xmpp.loop.create_task(self.make_caps())
        self.extra_info = extra_info

    def __repr__(self):
        return f"<LegacyContact '{self.jid}' ({self.user})>"

    @property
    def jid(self) -> JID:
        """
        Full JID (resource-included) of the contact
        """
        j = JID(self.jid_username + "@" + self.xmpp.boundjid.bare)
        j.resource = self.RESOURCE
        return j

    @property
    def jid_username(self) -> str:
        """
        The username part of the contact's legacy ID.
        Should be overridden to provide character escaping if required.
        """
        return self.legacy_id

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

    def send_message(self, body: str = "", chat_state: Optional[str] = "active"):
        """
        Transmit a message from the contact to the user

        :param body: Context of the message
        :param chat_state: By default, will send an "active" chat state (:xep:`0085`) along with the
            message. Set this to ``None`` if this is not desired.
        """
        msg = self.xmpp.make_message(mfrom=self.jid, mto=self.user.jid, mbody=body)
        if chat_state is not None:
            msg["chat_state"] = chat_state
        msg.send()
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


class BaseLegacyClient(ABC):
    """
    Abstract base class for communicating with the legacy network
    """

    def __init__(self, xmpp: BaseGateway):
        """
        :param xmpp: The gateway, to interact with the XMPP network
        """
        self.xmpp = xmpp
        LegacyContact.xmpp = xmpp

        xmpp["xep_0077"].api.register(self.user_validate, "user_validate")

        xmpp.add_event_handler("legacy_login", self.login)
        xmpp.add_event_handler("legacy_logout", self.logout)
        xmpp.add_event_handler("legacy_message", self.on_message)
        xmpp.add_event_handler("user_unregister", self.unregister)

    async def user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        log.debug("User validate: %s", (ifrom.bare, iq))
        form = iq["register"]["form"].get_values()

        for field in self.xmpp.REGISTRATION_FIELDS:
            if field.required and not form.get(field.name):
                raise XMPPError("Please fill in all fields", etype="modify")

        try:
            await self.validate(ifrom, form)
        except LegacyError as e:
            raise ValueError(f"Login Problem: {e}")
        else:
            user_store.add(ifrom, form)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited (fields name are restricted, single step only which
        is a problem for 2FA, SMS code auth...), it is OK to validate anything that looks good here
        and continue the registration progress via direct messages to the user (using :func:`.BaseGateway.input`
        for instance)

        :param user_jid:
        :param registration_form:
        """
        raise NotImplementedError

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

    async def on_message(self, msg: Message):
        """
        Called when the gateway user attempts to send a message through the gateway.

        :param msg:
        """
        raise NotImplementedError

    async def unregister(self, iq: Iq):
        """
        Called when the gateway user attempts to send a message through the gateway.

        :param iq:
        """
        raise NotImplementedError


log = logging.getLogger(__name__)
