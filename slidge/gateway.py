"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""
import hashlib
import logging
import re
import tempfile
from asyncio import Future
from pathlib import Path
from typing import Dict, Iterable, Optional, List, Any

import aiohttp
import qrcode
from slixmpp import ComponentXMPP, Message, Iq, JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.types import MessageTypes

from .db import user_store, RosterBackend, GatewayUser
from .legacy.session import BaseSession
from .util import FormField, SearchResult, ABCSubclassableOnceAtMost
from .types import AvatarType


class BaseGateway(ComponentXMPP, metaclass=ABCSubclassableOnceAtMost):
    """
    Class responsible for interacting with the gateway user ((un)registration) and dispatching
    messages from the user (or any slixmpp event) to the appropriate handlers.
    """

    REGISTRATION_FIELDS: Iterable[FormField] = [
        FormField(var="username", label="User name", required=True),
        FormField(var="password", label="Password", required=True),
    ]
    """
    Iterable of fields presented to the gateway user when registering using :xep:`0077`
    `extended <https://xmpp.org/extensions/xep-0077.html#extensibility>`_ by :xep:`0004`
    """
    REGISTRATION_INSTRUCTIONS: str = "Enter your credentials"

    COMPONENT_NAME: str = NotImplemented
    """Name of the component, as seen in service discovery"""
    COMPONENT_TYPE: Optional[str] = ""
    """Type of the gateway, should ideally follow https://xmpp.org/registrar/disco-categories.html"""
    COMPONENT_AVATAR: Optional[AvatarType] = None

    ROSTER_GROUP: str = "slidge"

    SEARCH_FIELDS: Iterable[FormField] = [
        FormField(var="first", label="First name", required=True),
        FormField(var="last", label="Last name", required=True),
    ]
    SEARCH_TITLE: str = "Search for legacy contacts"
    SEARCH_INSTRUCTIONS: str = ""

    def __init__(self, args):
        """

        :param args: CLI arguments parsed by :func:`.slidge.__main__.get_parser()`
        """
        super().__init__(
            args.jid,
            args.secret,
            args.server,
            args.port,
            plugin_whitelist=SLIXMPP_PLUGINS,
            plugin_config={
                "xep_0077": {
                    "form_fields": None,
                    "form_instructions": self.REGISTRATION_INSTRUCTIONS,
                },
                "xep_0100": {
                    "component_name": self.COMPONENT_NAME,
                    "user_store": user_store,
                    "type": self.COMPONENT_TYPE,
                },
                "xep_0184": {
                    "auto_ack": False,
                    "auto_request": True,
                },
                "xep_0363": {
                    "upload_service": args.upload_service,
                },
            },
        )
        self.home_dir = Path(args.home_dir)
        self._jid_validator = re.compile(args.user_jid_validator)
        self._config = args

        self._session_cls = BaseSession.get_unique_subclass()
        self._session_cls.xmpp = self

        self.register_plugins()
        self._register_slixmpp_api()
        self._register_handlers()
        self._input_futures: Dict[str, Future] = {}

    def _register_slixmpp_api(self):
        self["xep_0077"].api.register(
            user_store.get,
            "user_get",
        )
        self["xep_0077"].api.register(
            user_store.remove,
            "user_remove",
        )
        self["xep_0077"].api.register(
            self.make_registration_form,
            "make_registration_form",
        )
        self["xep_0077"].api.register(self._user_validate, "user_validate")
        self["xep_0077"].api.register(self._user_modify, "user_modify")

        self["xep_0055"].api.register(self._search_get_form, "search_get_form")
        self["xep_0055"].api.register(self._search_query, "search_query")

        self.roster.set_backend(RosterBackend)

    def _register_handlers(self):
        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("gateway_message", self.on_gateway_message)
        self.add_event_handler("user_register", self._on_user_register)
        self.add_event_handler("user_unregister", self._on_user_unregister)
        self.add_event_handler("legacy_login", self._legacy_login)
        get_session = self._session_cls.from_stanza

        # fmt: off
        async def logout(p): await get_session(p).logout(p)
        async def msg(m): await get_session(m).send_from_msg(m)
        async def disp(m): await get_session(m).displayed_from_msg(m)
        async def active(m): await get_session(m).active_from_msg(m)
        async def inactive(m): await get_session(m).inactive_from_msg(m)
        async def composing(m): await get_session(m).composing_from_msg(m)
        async def paused(m): await get_session(m).paused_from_msg(m)
        async def correct(m): await get_session(m).correct_from_msg(m)
        # fmt: on

        self.add_event_handler("legacy_logout", logout)
        self.add_event_handler("legacy_message", msg)
        self.add_event_handler("marker_displayed", disp)
        self.add_event_handler("chatstate_active", active)
        self.add_event_handler("chatstate_inactive", inactive)
        self.add_event_handler("chatstate_composing", composing)
        self.add_event_handler("chatstate_paused", paused)
        self.add_event_handler("message_correction", correct)

    def _add_commands(self):
        self["xep_0050"].add_command(
            node="info", name="Server Information", handler=self._handle_info
        )

    def _handle_info(self, iq: Iq, session: Dict[str, Any]):
        if iq.get_from().bare not in self._config.admins:
            raise XMPPError("not-authorized")
        form = self["xep_0004"].make_form("result", "Component info")
        form.add_field(
            ftype="jid-multi",
            label="Users",
            value=[u.bare_jid for u in user_store.get_all()],
        )

        session["payload"] = form
        session["has_next"] = False

        return session

    async def _validate_form(self, ifrom, form_dict):
        for field in self.REGISTRATION_FIELDS:
            if field.required and not form_dict.get(field.var):
                raise ValueError(f"Missing field: '{field.label}'")

        await self.validate(ifrom, form_dict)

    async def _user_validate(
        self, _gateway_jid, _node, ifrom: JID, form_dict: Dict[str, str]
    ):
        log.debug("User validate: %s", ifrom.bare)
        if not self._jid_validator.match(ifrom.bare):
            raise XMPPError(condition="not-allowed")
        await self._validate_form(ifrom, form_dict)
        log.info("New user: %s", ifrom.bare)
        user_store.add(ifrom, form_dict)

    async def _legacy_login(self, p: Presence):
        """
        Logs a :class:`slidge.BaseSession` instance to the legacy network

        :param p: Presence from a :class:`slidge.GatewayUser` directed at the gateway's own JID
        """
        session = self._session_cls.from_stanza(p)
        if not session.logged:
            session.logged = True
            await session.login(p)
            log.info("User logged in: %s", p.get_from().bare)

    async def _user_modify(
        self, _gateway_jid, _node, ifrom: JID, form_dict: Dict[str, str]
    ):
        user = user_store.get_by_jid(ifrom)
        log.debug("Modify user: %s", user)
        await self._validate_form(ifrom, form_dict)
        user_store.add(ifrom, form_dict)

    async def _on_user_unregister(self, iq: Iq):
        await self._session_cls.kill_by_jid(iq.get_from())

    async def _on_user_register(self, iq: Iq):
        for jid in self._config.admins:
            self.send_message(
                mto=jid, mbody=f"{iq.get_from()} has registered", mtype="headline"
            )

    async def _search_get_form(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        reply = iq.reply()
        form = reply["search"]["form"]
        form["title"] = self.SEARCH_TITLE
        form["instructions"] = self.SEARCH_INSTRUCTIONS
        for field in self.SEARCH_FIELDS:
            form.add_field(**field.dict())
        return reply

    async def _search_query(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError(text="Search is only allowed for registered users")

        result: SearchResult = await self._session_cls.from_stanza(iq).search(
            iq["search"]["form"].get_values()
        )

        if not result:
            raise XMPPError("item-not-found", text="Nothing was found")

        reply = iq.reply()
        form = reply["search"]["form"]
        for field in result.fields:
            form.add_reported(field.var, label=field.label, type=field.type)
        for item in result.items:
            form.add_item(item)
        return reply

    def config(self, argv: List[str]):
        """
        Override this to access CLI args to configure the slidge plugin

        :param argv: CLI args that were not parsed by Slidge
        """
        pass

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited in terms of validation, it is OK to validate
        anything that looks good here and continue the legacy auth process via direct messages
        to the user (using :func:`.BaseGateway.input` for instance)

        :param user_jid:
        :param registration_form:
        """
        pass

    async def unregister(self, user: GatewayUser):
        """
        Optionally override this if you need to clean additional
        stuff after a user has been removed from the user_store.

        :param user:
        :return:
        """
        pass

    async def on_session_start(self, event):
        log.debug("Gateway session start: %s", event)
        # prevents XMPP clients from considering the gateway as an HTTP upload
        await self["xep_0030"].del_feature(
            feature="urn:xmpp:http:upload:0", jid=self.boundjid.bare
        )
        for user in user_store.get_all():
            # We need to see which registered users are online, this will trigger legacy_login in return
            self["xep_0100"].send_presence(ptype="probe", pto=user.jid)
        self._add_commands()
        await self.make_vcard(self.boundjid.bare, self.COMPONENT_AVATAR)
        log.info("Slidge has successfully started")

    async def make_vcard(self, jid: JID, avatar: bytes):
        """
        Configure slixmpp to correctly set this contact's vcard (in fact only its avatar ATM)
        """
        vcard = self["xep_0054"].make_vcard()
        if avatar is not None:
            if isinstance(avatar, bytes):
                avatar_bytes = avatar
            elif isinstance(avatar, Path):
                with avatar.open("rb") as f:
                    avatar_bytes = f.read()
            elif isinstance(avatar, str):
                async with aiohttp.ClientSession() as session:
                    async with session.get(avatar) as response:
                        avatar_bytes = await response.read()
            else:
                raise TypeError("Avatar must be bytes, a Path or a str (URL)", avatar)
            vcard["PHOTO"]["BINVAL"] = avatar_bytes
            await self["xep_0153"].api["set_hash"](
                jid=jid, args=hashlib.sha1(avatar_bytes).hexdigest()
            )
        await self["xep_0054"].api["set_vcard"](
            jid=jid,
            args=vcard,
        )

    def shutdown(self):
        log.debug("Shutting down")
        for user in user_store.get_all():
            session = self._session_cls.from_jid(user.jid)
            for c in session.contacts:
                c.offline()
            self["xep_0100"].send_presence(ptype="unavailable", pto=user.jid)

    async def make_registration_form(self, _jid, _node, _ifrom, iq: Iq):
        if not self._jid_validator.match(iq.get_from().bare):
            raise XMPPError(condition="not-allowed")

        reg = iq["register"]
        user = user_store.get_by_stanza(iq)
        log.debug("User found: %s", user)

        form = reg["form"]
        form.add_field(
            "FORM_TYPE",
            ftype="hidden",
            value="jabber:iq:register",
        )
        form["title"] = f"Registration to '{self.COMPONENT_NAME}'"
        form["instructions"] = self.REGISTRATION_INSTRUCTIONS

        if user is not None:
            reg["registered"] = False
            form.add_field(
                "remove",
                label="Remove my registration",
                required=True,
                ftype="boolean",
                value=False,
            )

        for field in self.REGISTRATION_FIELDS:
            if field.var in reg.interfaces:
                val = None if user is None else user.get(field.var)
                if val is None:
                    reg.add_field(field.var)
                else:
                    reg[field.var] = val

        reg["instructions"] = self.REGISTRATION_INSTRUCTIONS

        for field in self.REGISTRATION_FIELDS:
            form.add_field(
                field.var,
                label=field.label,
                required=field.required,
                ftype=field.type,
                options=field.options,
                value=field.value if user is None else user.get(field.var, field.value),
            )

        reply = iq.reply()
        reply.set_payload(reg)
        return reply

    async def on_gateway_message(self, msg: Message):
        """
        Called when an XMPP user (not necessarily registered as a gateway user) sends a direct message to
        the gateway.

        If you override this and still want :func:`.input` to work, make sure to include the try/except part.

        :param msg: Message sent by the XMPP user
        """
        log.debug("Gateway msg: %s", msg)
        user = user_store.get_by_stanza(msg)
        if user is None:
            return
        try:
            f = self._input_futures.pop(user.bare_jid)
        except KeyError:
            r = msg.reply(body="I got that, but I'm not doing anything with it")
            r["type"] = "chat"
            self.send(r)
        else:
            f.set_result(msg["body"])

    async def input(
        self, user: GatewayUser, text=None, mtype: MessageTypes = "chat", **msg_kwargs
    ):
        """
        Request arbitrary user input using simple message stanzas, and await the result.

        :param user: The (registered) user we want input from
        :param text: A prompt to display for the user
        :param mtype:
        :return:
        """
        if text is not None:
            self.send_message(mto=user.jid, mbody=text, mtype=mtype, **msg_kwargs)
        f = self.loop.create_future()
        self._input_futures[user.bare_jid] = f
        await f
        return f.result()

    async def send_file(self, filename: str, **msg_kwargs):
        url = await self["xep_0363"].upload_file(filename=filename)
        msg = self.make_message(**msg_kwargs)
        msg["oob"]["url"] = url
        msg["body"] = url
        msg.send()

    async def send_qr(self, text: str, **msg_kwargs):
        qr = qrcode.make(text)
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            qr.save(f.name)
            await self.send_file(f.name, **msg_kwargs)


SLIXMPP_PLUGINS = [
    "xep_0050",  # Adhoc commands
    "xep_0054",  # vCard-temp
    "xep_0055",  # Jabber search
    "xep_0066",  # Out of Band Data
    "xep_0077",  # In-band registration
    "xep_0085",  # Chat state notifications
    "xep_0100",  # Gateway interaction
    "xep_0115",  # Entity capabilities
    "xep_0153",  # vCard-Based Avatars
    "xep_0184",  # Message Delivery Receipts
    "xep_0280",  # Carbons
    "xep_0308",  # Last message correction
    "xep_0333",  # Chat markers
    "xep_0334",  # Message Processing Hints
    "xep_0356",  # Privileged Entity  (different registration because not listed in slixmpp.plugins.__all__
    "xep_0363",  # HTTP file upload
]
log = logging.getLogger(__name__)
