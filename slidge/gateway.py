"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""
import logging
from abc import ABC
from asyncio import Future
from typing import Dict, Iterable, Optional, List

from slixmpp import ComponentXMPP, Message, Iq, JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from .db import user_store, RosterBackend, GatewayUser
from .util import get_unique_subclass, RegistrationField
from .legacy.session import BaseSession


class BaseGateway(ComponentXMPP, ABC):
    """
    Class responsible for interacting with the gateway user ((un)registration) and dispatching
    messages from the user (or any slixmpp event) to the appropriate handlers.
    """

    REGISTRATION_FIELDS: Iterable[RegistrationField] = [
        RegistrationField(name="username", label="User name", required=True),
        RegistrationField(name="password", label="Password", required=True),
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

    ROSTER_GROUP: str = "slidge"

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
                    "form_fields": [],
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
        self._session_cls = get_unique_subclass(BaseSession)
        self._session_cls.xmpp = self

        self.register_plugin("xep_0356")
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
        self.roster.set_backend(RosterBackend)

    def _register_handlers(self):
        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("gateway_message", self.on_gateway_message)
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

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        log.debug("User validate: %s", (ifrom.bare, iq))
        form = iq["register"]["form"].get_values()

        for field in self.REGISTRATION_FIELDS:
            if field.required and not form.get(field.name):
                raise XMPPError("Please fill in all fields", etype="modify")

        form_dict = {f.name: form.get(f.name) for f in self.REGISTRATION_FIELDS}

        try:
            await self.validate(ifrom, form_dict)
        except LegacyError as e:
            raise ValueError(f"Login Problem: {e}")
        else:
            user_store.add(ifrom, form)

    async def _legacy_login(self, p: Presence):
        """
        Logs a :class:`slidge.BaseSession` instance to the legacy network

        :param p: Presence from a :class:`slidge.GatewayUser` directed at the gateway's own JID
        """
        session = self._session_cls.from_stanza(p)
        if not session.logged:
            session.logged = True
            await session.login(p)

    async def _on_user_unregister(self, iq: Iq):
        user = user_store.get_by_stanza(iq)
        if user is None:
            raise KeyError("Cannot find user", user)
        await self.unregister(user, iq)

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

    async def unregister(self, user: GatewayUser, iq: Iq):
        """
        Called when the user unregister from the gateway

        :param user:
        :param iq:
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

    async def make_registration_form(self, _jid, _node, _ifrom, iq: Iq):
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
            if field.name in reg.interfaces:
                val = None if user is None else user.get(field.name)
                if val is None:
                    reg.add_field(field.name)
                else:
                    reg[field.name] = val

        reg["instructions"] = self.REGISTRATION_INSTRUCTIONS

        for field in self.REGISTRATION_FIELDS:
            field.value = (
                field.value if user is None else user.get(field.name, field.value)
            )
            form.add_field(
                field.name,
                label=field.label,
                required=field.required,
                ftype=field.type,
                value=field.value,
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
            self.send(msg.reply(body="I got that, but I'm not doing anything with it"))
        else:
            f.set_result(msg["body"])

    async def input(self, user: GatewayUser, text=None):
        """
        Request arbitrary user input using simple message stanzas, and await the result.

        :param user: The (registered) user we want input from
        :param text: A prompt to display for the user
        :return:
        """
        if text is not None:
            self.send_message(mto=user.jid, mbody=text)
        f = self.loop.create_future()
        self._input_futures[user.bare_jid] = f
        await f
        return f.result()


SLIXMPP_PLUGINS = [
    "xep_0054",  # vCard-temp
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
    # "xep_0356",  # Privileged Entity  (different registration because not listed in slixmpp.plugins.__all__
    "xep_0363",  # HTTP file upload
]
log = logging.getLogger(__name__)
