"""
This module extends slixmpp.ComponentXMPP to make writing new LegacyClients easier
"""
import dataclasses
import logging
from asyncio import Future
from typing import Dict, Iterable

from slixmpp import ComponentXMPP, Message, Iq

from .db import user_store, RosterBackend, GatewayUser


@dataclasses.dataclass
class RegistrationField:
    name: str
    label: str = None
    required: bool = True
    private: bool = False
    type: str = None
    value: str = ""


class BaseGateway(ComponentXMPP):
    REGISTRATION_FIELDS: Iterable[RegistrationField] = [
        RegistrationField(name="username", label="Legacy user name"),
        RegistrationField(name="password", label="Legacy password"),
        RegistrationField(
            name="something_else",
            label="Some optional stuff not covered by jabber:iq:register",
            required=False,
        ),
    ]
    """Set of fields presented to the gateway user when registering using :xep:`0077`"""
    REGISTRATION_INSTRUCTIONS: str = "Enter your legacy credentials"

    COMPONENT_NAME: str = "SliXMPP gateway"
    """Name of the component, as seen in service discovery"""
    COMPONENT_TYPE: str = ""
    """Type of the gateway, should ideally follow https://xmpp.org/registrar/disco-categories.html"""

    PLUGINS = {
        "xep_0054",  # vCard-temp
        "xep_0085",  # Chat state notifications
        "xep_0115",  # Entity capabilities
        "xep_0153",  # vCard-Based Avatars
        "xep_0280",  # Carbons
        "xep_0333",  # Chat markers
        "xep_0334",  # Message Processing Hints
        "xep_0356",  # Privileged Entity
    }

    ROSTER_GROUP = "slidge"

    def __init__(self, jid: str, secret: str, server: str, port: str):
        """
        :param jid: The gateway's JID
        :param secret: The gateway's secret
        :param server: The XMPP server to connect to
        :param port: The port used by the XMPP server to accept component connections
        """
        super().__init__(jid, secret, server, port)
        self.input_futures: Dict[str, Future] = {}

        for p in self.PLUGINS:
            self.register_plugin(p)

        log.debug("%s", [f.name for f in self.REGISTRATION_FIELDS if f.required])
        self.register_plugin(
            "xep_0077",
            pconfig={
                "form_fields": [],
                "form_instructions": self.REGISTRATION_INSTRUCTIONS,
            },
        )

        self.register_plugin(
            "xep_0100",
            pconfig={
                "component_name": self.COMPONENT_NAME,
                "user_store": user_store,
                "type": self.COMPONENT_TYPE,
            },
        )

        self.register_plugin(
            "xep_0184",  # Message Delivery Receipts
            pconfig={
                "auto_ack": False,
                "auto_request": True,
            },
        )

        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("gateway_message", self.on_gateway_message)

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

        self.roster.set_backend(RosterBackend)

    async def on_session_start(self, event):
        log.debug("Gateway session start: %s", event)
        for jid in user_store.users:
            # We need to see which registered users are online, this will trigger legacy_login in return
            self["xep_0100"].send_presence(ptype="probe", pto=jid)

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

        if user is None:
            user = {}
        else:
            reg["registered"] = False
            form.add_field(
                "remove",
                label="Remove my registration",
                required=True,
                ftype="boolean",
                value=False
            )

        for field in self.REGISTRATION_FIELDS:
            if field.name in reg.interfaces:
                val = user.get(field.name)
                if val is None:
                    reg.add_field(field.name)
                else:
                    reg[field.name] = val

        reg["instructions"] = self.REGISTRATION_INSTRUCTIONS

        for field in self.REGISTRATION_FIELDS:
            field.value = user.get(field.name, field.value)
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
        try:
            f = self.input_futures.pop(user.bare_jid)
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
        f = Future()
        self.input_futures[user.bare_jid] = f
        await f
        return f.result()

    def ack(self, msg: Message):
        """
        Send a message receipt (:xep:`0184`) in response to a message sent by a gateway user

        :param msg: The message to ack
        """
        self["xep_0184"].ack(msg)


RESOURCE = "slidge"
log = logging.getLogger(__name__)
