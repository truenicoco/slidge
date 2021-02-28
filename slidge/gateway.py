import asyncio
import logging
import typing
import time
import hashlib
from configparser import ConfigParser
from functools import partial
from pathlib import Path

from slixmpp import ComponentXMPP, Message, Iq, Presence, JID
from slixmpp.exceptions import XMPPError
from slixmpp.thirdparty import OrderedSet

from slidge.base_legacy import BaseLegacyClient, LegacyError
from slidge.database import init_session, User, RosterBackend
from slidge.session import sessions
from slidge.buddy import Buddy
from slidge.muc import LegacyMuc

from slidge.plugins import xep_0100


class BaseGateway(ComponentXMPP):
    """
    A component that can act as an XMPP gateway to a legacy network

    At the very least, a gateway should subclass me and override the relevant
    class attributes.

    Further personalisation of the component's behaviour can be achieved
    easily by plugging methods and using SliXMPP event handling features.
    """

    PLUGINS = {
        "xep_0045",  # MUC
        "xep_0050",  # Ad-hoc commands
        "xep_0054",  # vcard temp
        "xep_0085",  # Chat state notifications
        "xep_0086",  # Errors
        "xep_0115",  # Entity capabilities
        "xep_0128",  # Service Discovery Extensions
        "xep_0153",  # vCard-Based Avatars
        "xep_0184",  # Message Delivery Receipts
        "xep_0249",  # Direct MUC invitations
        "xep_0280",  # Carbons
        "xep_0333",  # Chat markers
        "xep_0334",  # Message Processing Hints
        "xep_0356",  # Privileged Entity
        "xep_0363",  # HTTP upload
    }
    """
    Set of slixmpp plugins to load when initializing the gateway.
    This shouln't need to be overriden.
    """
    REGISTRATION_FIELDS: OrderedSet = OrderedSet(["username", "password"])
    """Set of fields presented to the gateway user when registering using :xep:`0077`"""
    REGISTRATION_INSTRUCTIONS: str = "Enter your legacy credentials"

    COMPONENT_NAME: str = "SliXMPP gateway"
    """Name of the component, as seen in service discovery"""
    COMPONENT_TYPE: str = ""
    """Type of the gateway, should ideally follow https://xmpp.org/registrar/disco-categories.html"""

    # FIXME: replace with a logo, if we ever have one
    AVATAR: typing.Optional[Path] = (
        Path(__file__).parent.parent / "assets" / "gateway.png"
    )
    """Path to an image that serves as the avatar of the gateway component"""

    def __init__(self, config: ConfigParser, client_cls=BaseLegacyClient):
        """
        :param config: `configuration example`_
        :param client_cls: the legacy client constructor, a subclass of :class:`BaseLegacyClient`
        """
        ComponentXMPP.__init__(
            self,
            config["component"]["jid"],
            config["component"]["secret"],
            config["component"]["server"],
            config["component"]["port"],
        )
        self.config = config

        sessions.xmpp = self

        legacy_client = client_cls()
        legacy_client.xmpp = self
        legacy_client.config = config["legacy"]
        self.legacy_client = legacy_client

        self._prompt_futures = dict()


        self.register_plugin(
            "xep_0077",
            pconfig={
                "form_fields": self.REGISTRATION_FIELDS,
                "form_instructions": self.REGISTRATION_INSTRUCTIONS,
            },
        )

        self.register_plugin(
            "xep_0100",
            pconfig={
                "component_name": self.COMPONENT_NAME,
                "user_store": User,
                "type": self.COMPONENT_TYPE,
            },
        )

        for xep in self.PLUGINS:
            self.register_plugin(xep)

        # Fix for upload service discovery
        self["xep_0030"].wrap_results = True

        # We want to ack messages using from=legacy_user@gateway and
        # only when triggered by legacy client.
        self["xep_0184"].auto_ack = False

        self._init_db()
        self._init_event_handlers()
        self.roster.set_backend(RosterBackend(self.boundjid.bare))

        # self.use_origin_id = False

    def _init_db(self):
        init_session(
            self.config["database"].get("path"),
            self.config["database"].getboolean("echo"),
        )
        self["xep_0077"].api.register(
            self._user_validate,
            "user_validate",
        )
        self["xep_0077"].api.register(
            self._user_get,
            "user_get",
        )
        self["xep_0077"].api.register(
            self._user_remove,
            "user_remove",
        )
        # Waiting for the async internal API merge
        # self["xep_0077"]._user_validate = self._user_validate

    def _init_event_handlers(self):
        self.add_event_handler("session_start", self._startup)

        self.add_event_handler("legacy_message", self._on_legacy_message)
        self.add_event_handler("legacy_login", self._on_legacy_login)
        self.add_event_handler("legacy_logout", self._on_legacy_logout)
        self.add_event_handler("gateway_message", self._set_prompt_result)

        # functools.partial returns callables on which iscouroutinefunction returns False
        # https://bugs.python.org/issue23519
        # async lambda are only available in python 3.8+
        # https://bugs.python.org/issue33447
        # so let's use some good old async defs here, even if this feels a bit verbose

        async def buddy_receipt(msg):
            await self._dispatch_buddy_msg(msg, Buddy.send_legacy_receipt)

        async def buddy_composing(msg):
            await self._dispatch_buddy_msg(msg, Buddy.send_legacy_composing)

        async def buddy_pause(msg):
            await self._dispatch_buddy_msg(msg, Buddy.send_legacy_pause)

        async def buddy_read(msg):
            await self._dispatch_buddy_msg(msg, Buddy.send_legacy_read_mark)

        self.add_event_handler("receipt_received", buddy_receipt)
        self.add_event_handler("chatstate_composing", buddy_composing)
        self.add_event_handler("marker_displayed", buddy_read)
        self.add_event_handler("chatstate_paused", buddy_pause)

        async def groupchat_message(msg):
            await self._dispatch_groupchat_msg(msg, LegacyMuc.from_user)

        self.add_event_handler("groupchat_join", self._on_groupchat_join)
        self.add_event_handler("groupchat_message", groupchat_message)

    async def _startup(self, event):
        await self._make_avatar()
        for jid in self.client_roster:
            self["xep_0100"].send_presence(pto=jid)
            self["xep_0100"].send_presence(pto=jid, ptype="probe")

    async def _make_avatar(self):
        if self.AVATAR is not None:
            with self.AVATAR.open("rb") as fp:
                avatar_bytes = fp.read()
            vcard = self["xep_0054"].make_vcard()
            vcard["PHOTO"]["BINVAL"] = avatar_bytes
            await self["xep_0153"].api["set_hash"](
                jid=self.jid, args=hashlib.sha1(avatar_bytes).hexdigest()
            )
            await self["xep_0054"].api["set_vcard"](
                jid=self.jid,
                args=vcard,
            )

    async def _on_groupchat_join(self, presence: Presence):
        user_jid = presence["from"]
        user = User.by_jid(user_jid)
        if user is None:
            return

        muc_node = presence["to"].username
        log.debug(f"{user} wants to join {muc_node}")
        await sessions.by_jid(user_jid).mucs.by_jid_node(muc_node).user_join(presence)

    async def _on_legacy_login(self, stanza):
        user = User.by_jid(stanza["from"])
        await sessions[user].login()

    async def _set_prompt_result(self, msg: Message):
        try:
            future = self._prompt_futures.pop(msg["from"].bare)
        except KeyError:
            pass
        else:
            future.set_result(msg["body"])

    async def _on_legacy_logout(self, stanza):
        try:
            await sessions.by_jid(stanza["from"]).logout()
        except KeyError as e:
            log.error(f"Error while legacy logout: {e}")

    async def _on_legacy_message(self, msg: Message):
        if msg["type"] == "groupchat":
            raise NotImplementedError
        else:
            if msg["body"] != "":
                await self._dispatch_buddy_msg(msg, Buddy.send_legacy_message)

    async def _dispatch_buddy_msg(self, msg: Message, coroutine: typing.Coroutine):
        if msg["to"] == self.boundjid.bare:
            return
        if msg["type"] == "groupchat":
            return
        log.debug(f"Dispatching to {coroutine}")
        session = sessions.by_jid(msg["from"])
        buddy = session.buddies.by_jid(msg["to"])
        try:
            return await coroutine(buddy, msg)
        except LegacyError as e:
            raise XMPPError(text=e.msg)

    async def _dispatch_groupchat_msg(self, msg: Message, coroutine: typing.Coroutine):
        session = sessions.by_jid(msg["from"])
        muc = session.mucs.by_jid_node(msg["to"].node)
        try:
            return await coroutine(muc, msg)
        except LegacyError as e:
            raise XMPPError(text=e.msg)

    async def _user_validate(self, jid, node, ifrom, reg):
        await self.legacy_client.validate(ifrom, reg)
        user = User(
            jid=ifrom, legacy_id=reg["username"], legacy_password=reg["password"]
        )
        user.commit()

    async def _user_get(self, jid, node, ifrom, stanza):
        return User.by_jid(stanza["from"])

    async def _user_remove(self, jid, node, ifrom, stanza):
        sessions.destroy_by_jid(stanza["from"])
        user = User.by_jid(stanza["from"])
        user.delete()

    async def prompt(self, jid: JID, **kwargs):
        future = self.loop.create_future()
        self._prompt_futures[jid.bare] = future
        self.send_message(mto=jid, **kwargs)
        return await future

    async def shutdown(self):
        await sessions.shutdown()


log = logging.getLogger(__name__)
