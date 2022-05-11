"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict

from slixmpp import Message, JID, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from slidge import BaseLegacyClient, LegacyContact, user_store, BaseGateway


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist."
    )


class LegacyClient(BaseLegacyClient):
    def __init__(self, xmpp: Gateway):
        super().__init__(xmpp)
        self.xmpp.add_event_handler("marker_displayed", self.on_marker_displayed)
        self.xmpp.add_event_handler("chatstate_composing", self.on_user_composing)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        if registration_form["username"] != "n":
            raise LegacyError("Y a que N!")

    async def login(self, p: Presence):
        if p.get_to() != self.xmpp.boundjid.bare:
            log.debug("Ignoring presence sent to buddy")
            return
        user = user_store.get_by_stanza(p)

        for b, a in zip(BUDDIES, AVATARS):
            c = LegacyContact(user, b.lower(), b.title(), avatar=a)
            await c.add_to_roster()
            c.online()

    async def logout(self, p: Presence):
        user = user_store.get_by_stanza(p)
        user.resources.remove(p.get_from().resource)

    async def on_message(self, msg: Message):
        user = user_store.get_by_stanza(msg)
        contact = LegacyContact(user, str(msg.get_to().username))

        if contact.legacy_id not in BUDDIES:
            raise XMPPError(text="Contact does not exist")

        self.xmpp.ack(msg)
        contact.ack(msg)
        await asyncio.sleep(1)

        contact.displayed(msg)
        await asyncio.sleep(1)

        contact.active()
        # not sure why this does not work in gajim. it seems to work with telegram and signal...
        contact.composing()
        await asyncio.sleep(1)

        reply = contact.send_message("OK")

        # useful to transport "read mark" to the legacy network
        log.debug("Sent message ID: %s", reply["id"])

        async def later():
            await asyncio.sleep(1)
            contact.inactive()

        self.xmpp.loop.create_task(later())

    @staticmethod
    async def on_marker_displayed(msg: Message):
        log.debug("Marker: %s", msg)

    @staticmethod
    async def on_user_composing(p: Presence):
        user = user_store.get_by_stanza(p)
        log.debug("%s is composing", user)


ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"

BUDDIES = ["baba", "bibi"]
AVATARS = []

with (ASSETS_DIR / "buddy1.png").open("rb") as fp:
    AVATARS.append(fp.read())

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())

log = logging.getLogger(__name__)
