"""
Operations related to buddies (contacts) on the legacy network.
"""

# for self.xmpp["xep_XXXX"] checks
# pylint: disable=unsubscriptable-object

import logging
import typing
import base64
import hashlib
import asyncio
import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from slixmpp import JID, Message, Iq, ComponentXMPP
from slixmpp.roster import RosterItem

from slidge.util import escape, pprint
from slidge.database import User


class Buddy:
    """
    Represents a buddy (or contact) in the legacy network.

    Should only be instantiated directly when the legacy client returns the list
    of legacy contacts of XMPP user. Besides this case, it should be instantiated
    using the `buddies` attributes of the the `Session` class.

    :param str legacy_id: Identifier (username, phone number…) of the
        contact on the legacy network
    :param str name: Human readable name of the legacy buddy, optional
    :param slidge.gateway.BaseGateway xmpp: The gateway component, optional
    :param User user: The registered gateway user.
    """

    IDENTITY_CATEGORY = "client"
    IDENTITY_TYPE = "bot"

    def __init__(self, legacy_id, ptype="available"):
        from slidge.gateway import BaseGateway

        self.legacy_id = legacy_id
        self.name: str = ""

        self._ptype: str = ptype
        self.xmpp: typing.Optional[BaseGateway] = None
        self.avatar_bytes: typing.Optional[bytes] = None
        self.user: typing.Optional[User] = None

    def __repr__(self):
        return f"<Buddy '{self.legacy_id}' ({self.jid})>"

    async def finalize(self):
        """
        Create identity, caps and vcard.

        Called by `Buddies` on sync, no need to call this manually
        """
        log.debug(f"Finalizing {self}")
        self._make_roster_entry()
        self._make_identity()
        await self._make_vcard()
        await self._update_caps()

    def _make_roster_entry(self):
        self.xmpp.roster[self.jid].add(self.user.jid, ato=True, afrom=True, save=True)

    async def _make_vcard(self):
        vcard = self.xmpp["xep_0054"].make_vcard()
        if self.avatar_bytes is not None:
            vcard["PHOTO"]["BINVAL"] = self.avatar_bytes
            await self.xmpp["xep_0153"].api["set_hash"](
                jid=self.jid, args=hashlib.sha1(self.avatar_bytes).hexdigest()
            )
        await self.xmpp["xep_0054"].api["set_vcard"](
            jid=self.jid,
            args=vcard,
        )

    def _make_identity(self):
        log.debug(f"Making identity of {self}")
        self.xmpp["xep_0030"].add_identity(
            jid=self.jid, category=self.IDENTITY_CATEGORY, itype=self.IDENTITY_TYPE
        )

    async def _update_caps(self):
        log.debug(f"Updating caps of {self}")
        # FIXME: which features are really needed here?
        for f in (
            "jabber:iq:oob",
            "jabber:x:oob",
            "jabber:x:data",
            "http://jabber.org/protocol/chatstates",
            "urn:xmpp:http:upload:0",
            "vcard-temp",
            "urn:xmpp:receipts",
        ):
            await self.xmpp["xep_0030"].add_feature(feature=f, jid=self.jid)
        info = await self.xmpp['xep_0030'].get_info(self.jid, node=None, local=True)
        if isinstance(info, Iq):
            info = info['disco_info']
        ver = self.xmpp['xep_0115'].generate_verstring(info, self.xmpp['xep_0115'].hash)
        await self.xmpp['xep_0030'].set_info(
            jid=self.jid,
            node='%s#%s' % (self.xmpp['xep_0115'].caps_node, ver),
            info=info
        )
        await self.xmpp['xep_0115'].cache_caps(ver, info)
        await self.xmpp['xep_0115'].assign_verstring(self.jid, ver)

        # Broadcasting now results in bare JID presences from legacy contacts
        # that gajim does not like
        # self.xmpp["xep_0115"].broadcast = False
        # await self.xmpp["xep_0030"].update_caps(jid=self.jid)
        # self.xmpp["xep_0115"].broadcast = True

    @property
    def ptype(self):
        return self._ptype

    @ptype.setter
    def ptype(self, type: str):
        """
        Sets the buddy presence type and broadcast it to XMPP
        """
        self._ptype = type
        self.send_xmpp_presence(ptype=type)

    @property
    def legacy(self):
        """Shortcut for legacy client access"""
        return self.xmpp.legacy_client

    @property
    def jid(self) -> JID:
        """
        A full JID that will be used to construct stanzas emanating from this
        legacy buddy, including the resource part specified in the config file.
        """
        jid = JID()
        jid.node = escape(self.legacy_id)
        jid.server = self.xmpp.boundjid.bare
        jid.resource = self.xmpp.config["buddies"]["resource"]
        return jid

    def make_xmpp_message(
        self, mtype="chat", bare_jid=False, chat_state=None
    ) -> Message:
        """
        Prepare an slixmpp message object emanating from this legacy buddy.

        :param str mtype: Can be "chat" or "groupchat"
        :param bool bare_jid: If true, the message "from" JID will not include
            the buddy's resource
        :param chat_state: The XEP-0085 chat state ("active", "inactive", "gone",
            "composing" or "paused"), optional
        :ptype chat_state: str or None

        :return: A message with fields "to", "from", "type" filled, and optionaly
            a chat state
        :rtype: Message
        """
        msg = self.xmpp.make_message(
            mto=self.user.jid,
            mfrom=self.jid.bare if bare_jid else self.jid,
            mtype=mtype,
        )
        msg["chat_state"] = chat_state
        return msg

    def send_xmpp_message(self, body: str) -> Message:
        """
        Send a message from this legacy buddy to the gateway user.

        Called by the gateway when the gateway user receives a legacy message
        from this legacy buddy.

        :param str body: Body of the message

        :return: The message that will be sent, whose id can be used to send
            read markers to the legacy network
        :rtype: Message
        """
        msg = self.make_xmpp_message(chat_state="active")
        msg["body"] = body
        msg["request_receipt"] = True
        msg.send()
        return msg

    def send_xmpp_composing(self):
        """
        Transport the 'buddy is typing' notification from the legacy network to
        the XMPP user.
        """
        self.make_xmpp_message(chat_state="composing").send()

    def send_xmpp_inactive(self):
        """
        Transport the 'buddy stopped typing' notification from the legacy network to
        the XMPP user.
        """
        self.make_xmpp_message(chat_state="inactive").send()

    def send_xmpp_ack(self, msg: Message):
        """
        Should be called when the legacy buddy's client has acked the XMPP user message.

        :param Message msg: The message to ack
        """
        log.debug(f"Acking message {msg['id']}")
        ack = self.make_xmpp_message()
        ack["receipt"] = msg["id"]
        ack.send()
        # FIXME: 'retrieved' marker
        # self.xmpp["xep_0333"].send_marker(
        #     mto=msg["from"], id=msg["id"], marker="retrieved", mfrom=msg["to"]
        # )

    def send_xmpp_read(self, msg: Message):
        """
        Called when the legacy buddy's client has read the XMPP user message.

        :param Message msg: The message to mark as read
        """
        self.make_xmpp_message(chat_state="active").send()
        self.xmpp["xep_0333"].send_marker(
            mto=msg["from"], id=msg["id"], marker="displayed", mfrom=msg["to"]
        )

    def send_xmpp_presence(self, bare=False, **kwargs):
        """
        Sends a presence from the legacy buddy to the XMPP user, including working
        XEP-0115 capabilities.

        :param kwargs: additional arguments passed to `gateway.make_presence()`
        """
        p = self.xmpp.make_presence(
            pfrom=self.jid.bare if bare else self.jid, pto=self.user.jid, **kwargs
        )
        # log.debug(f"{self}: sending {p}")
        p.send()

    def send_xmpp_carbon(self, body: str, timestamp: datetime.datetime):
        """
        Sync a message sent from an official client by the gateway user to XMPP.

        Uses XEP-0356 to impersonate the XMPP user and send a carbon message.

        :param str body: Body of the message
        :param str timestamp_iso: ISO 8601 formatted timestamp, just like XMPP wants
        """
        # FIXME: timestamp does not seem to work in gajim
        # we use Message() directly because we need xmlns="jabber:client"
        msg = Message()
        msg["from"] = self.user.jid.bare
        msg["to"] = self.jid.bare
        msg["type"] = "chat"
        msg["body"] = body
        msg["delay"].set_stamp(
            timestamp.isoformat()[:19] + "Z"
        )  # pylint: disable=no-member

        carbon = Message()
        carbon["from"] = self.user.jid.bare
        carbon["to"] = self.user.jid.bare
        carbon["type"] = "chat"
        carbon["carbon_sent"] = msg
        carbon.enable("no-copy")
        # carbon["delay"].set_stamp(timestamp.isoformat()[:19] + "Z")  # pylint: disable=no-member

        self.xmpp["xep_0356"].send_privileged_message(carbon)

    async def send_legacy_receipt(self, receipt: Message):
        """
        Sends a "legacy ack" from the XMPP user to the legacy buddy.

        :param Message receipt: The receipt
        """
        await self.legacy.send_receipt(user=self.user, receipt=receipt)

    async def send_legacy_read_mark(self, marker: Message):
        """
        Sends a "legacy read marker" from the XMPP user to the legacy buddy.

        :param Message receipt: a message including the "displayed" marker
        """
        await self.legacy.send_read_mark(
            user=self.user,
            legacy_buddy_id=self.legacy_id,
            msg_id=marker["displayed"]["id"],
        )

    async def send_legacy_message(self, msg: Message):
        """
        Sends a message send by the gateway user to the legacy network.

        :param Message msg: Message sent by the XMPP user to the gateway
        """
        await self.legacy.send_message(
            user=self.user, legacy_buddy_id=self.legacy_id, msg=msg
        )

    async def send_legacy_composing(self, msg: Message):
        """
        Sends a "composing" notification from the XMPP user to the legacy buddy.
        """
        await self.legacy.send_composing(user=self.user, legacy_buddy_id=self.legacy_id)

    async def send_legacy_pause(self, msg: Message):
        """
        Sends a "composing pause" notification from the XMPP user to the legacy buddy.
        """
        await self.legacy.send_pause(user=self.user, legacy_buddy_id=self.legacy_id)


class Buddies:
    """
    Represents the "legacy roster" of the XMPP user.

    Should be accessed like a dict, using a buddy legacy id as key.
    If the buddy instance exists, it is returned, if not, it is created.
    """

    def __init__(self):
        # avoid circular import
        from slidge.gateway import BaseGateway

        self.user: User = None
        self.xmpp: BaseGateway = None
        self._buddies_by_legacy_id: typing.Dict[str, Buddy] = {}
        self._buddies_by_bare_jid: typing.Dict[str, Buddy] = {}

    def __iter__(self) -> typing.Iterable[Buddy]:
        """Iterator over legacy roster users"""
        return iter(self._buddies_by_legacy_id.values())

    def _roster_items(self, subscription: str) -> typing.Dict:
        return {
            buddy.jid.bare: {
                "name": buddy.name,
                "subscription": subscription,
                "groups": [self.xmpp.config["buddies"]["group"]],
            }
            for buddy in self
        }

    def by_jid(self, jid: JID) -> Buddy:
        """
        Returns a buddy instance by matching its bare JID.

        In case the user cannot be found in `self.buddies`, returns a new Buddy
        using the username part of the JID as legacy identifier.
        Should work as long as there legacy identifiers are valid JID usernames.

        :param jid: JID of the legacy buddy
        """
        try:
            return self._buddies_by_bare_jid[jid.bare]
        except KeyError:
            try:
                return self._buddies_by_legacy_id[jid.username]
            except KeyError:
                buddy = Buddy(legacy_id=jid.username)
                self.add(buddy)
                return buddy

    def by_legacy_id(self, legacy_id: str) -> Buddy:
        """
        Returns a buddy instance by matching its legacy id.

        :param legacy_id: Identifier of the buddy on the legacy network
        """
        try:
            return self._buddies_by_legacy_id[legacy_id]
        except KeyError:
            # TODO: Async update of username (here?)
            buddy = Buddy(legacy_id=legacy_id)
            self.add(buddy)
            return buddy

    def add(self, buddy: Buddy):
        """
        Adds a buddy to the legacy roster, correctly setting its xmpp and user
        attributes.

        :param Buddy buddy: The legacy buddy to add
        """
        buddy.xmpp = self.xmpp
        buddy.user = self.user
        self._buddies_by_legacy_id[buddy.legacy_id] = buddy
        self._buddies_by_bare_jid[buddy.jid.bare] = buddy

    @property
    def legacy(self):
        """Shortcut to access the legacy client."""
        return self.xmpp.legacy_client

    async def sync(self):
        """
        Sync the legacy roster to this by calling get_buddies on the legacy client.
        """
        for buddy in await self.legacy.get_buddies(self.user):
            self.add(buddy)
        log.debug(f"Filling roster of {self.user}")
        await self.fill_roster()
        log.debug(f"Finalizing buddies of {self.user}")
        log.debug(f"Sending buddies presences for {self.user}: {list(self)}")
        for buddy in self:
            log.debug(f"{buddy}")
            await buddy.finalize()
            buddy.send_xmpp_presence(ptype=buddy.ptype)

    async def _roster_manipulation(self, subscription):
        await self.xmpp["xep_0356"].set_roster(
            jid=self.user.jid.bare, roster_items=self._roster_items(subscription)
        )

    async def fill_roster(self):
        """
        Populate the XMPP user roster using XEP-0356
        """
        await self._roster_manipulation("both")

    async def empty_roster(self):
        """
        Empties the XMPP user roster using XEP-0356, to be called when user
        unsubscribes from the gateway
        """
        await self._roster_manipulation("remove")

    def shutdown(self):
        """
        Sends offline for all buddies listed here when the gateway shuts down.
        """
        for buddy in self:
            buddy.send_xmpp_presence(ptype="unavailable")


log = logging.getLogger(__name__)
