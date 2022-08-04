import logging

from slixmpp import Iq, Message, Presence
from slixmpp.plugins.base import BasePlugin

log = logging.getLogger(__name__)


class XEP_0100(BasePlugin):
    name = "xep_0100"
    description = "XEP-0100: Gateway interaction (slidge)"
    dependencies = {
        "xep_0030",  # Service discovery
        "xep_0077",  # In band registration
        "xep_0356",  # Privileged entities
    }

    default_config = {
        "component_name": "SliXMPP gateway",
        "type": "xmpp",
        "needs_registration": True,
    }

    def plugin_init(self):
        if not self.xmpp.is_component:
            log.error("Only components can be gateways, aborting plugin load")
            return

        self.xmpp["xep_0030"].add_identity(
            name=self.component_name, category="gateway", itype=self.type
        )

        # Without that BaseXMPP sends unsub/unavailable on sub requests, and we don't want that
        self.xmpp.client_roster.auto_authorize = False
        self.xmpp.client_roster.auto_subscribe = False

        self.xmpp.add_event_handler("user_register", self.on_user_register)
        self.xmpp.add_event_handler("user_unregister", self.on_user_unregister)
        self.xmpp.add_event_handler(
            "presence_unsubscribe", self.on_presence_unsubscribe
        )

        self.xmpp.add_event_handler("message", self.on_message)

    def plugin_end(self):
        if not self.xmpp.is_component:
            self.xmpp.remove_event_handler("user_register", self.on_user_register)
            self.xmpp.remove_event_handler("user_unregister", self.on_user_unregister)
            self.xmpp.remove_event_handler(
                "presence_unsubscribe", self.on_presence_unsubscribe
            )

            self.xmpp.remove_event_handler("message", self.on_message)

    async def get_user(self, stanza):
        return await self.xmpp["xep_0077"].api["user_get"](None, None, None, stanza)

    async def on_user_unregister(self, iq: Iq):
        self.xmpp.send_presence(pto=iq.get_from().bare, ptype="unavailable")
        self.xmpp.send_presence(pto=iq.get_from().bare, ptype="unsubscribe")
        self.xmpp.send_presence(pto=iq.get_from().bare, ptype="unsubscribed")

    async def on_user_register(self, iq: Iq):
        self.xmpp.client_roster[iq.get_from()].load()
        await self.xmpp["xep_0356"].set_roster(
            jid=iq.get_from().bare,
            roster_items={
                self.xmpp.boundjid.bare: {
                    "name": self.component_name,
                    "subscription": "both",
                    "groups": ["Slidge"],
                }
            },
        )

    def on_presence_unsubscribe(self, p: Presence):
        if p.get_to() == self.xmpp.boundjid.bare:
            log.debug("REMOVE: Our roster: %s", self.xmpp.client_roster)
            self.xmpp["xep_0077"].api["user_remove"](None, None, p["from"], p)
            self.xmpp.event("user_unregister", p)

    async def on_message(self, msg: Message):
        if msg["type"] == "groupchat":
            return  # groupchat messages are out of scope of XEP-0100

        if msg["to"] == self.xmpp.boundjid.bare:
            # It may be useful to exchange direct messages with the component
            self.xmpp.event("gateway_message", msg)
            return

        if self.needs_registration and await self.get_user(msg) is None:
            return

        self.xmpp.event("legacy_message", msg)
