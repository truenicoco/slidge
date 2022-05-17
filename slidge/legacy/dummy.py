"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict

from slixmpp import Message, JID, Presence, Iq
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from slidge import *


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist.\n"
        "You can use any password you want."
    )


class Session(BaseSession):
    def __init__(self, xmpp, user):
        super(Session, self).__init__(xmpp, user)
        self.counter = 0

    @staticmethod
    def create(xmpp: BaseGateway, user: GatewayUser) -> "BaseSession":
        s = Session(xmpp, user)
        return s

    async def login(self, p: Presence):
        self.logged = True
        for b, a in zip(BUDDIES, AVATARS):
            c = self.contacts.by_legacy_id(b.lower())
            c.name = b.title()
            c.avatar = a
            await c.add_to_roster()
            c.online()

    async def logout(self, p: Presence):
        log.debug("User has disconnected")

    async def send(self, msg: Message, contact: LegacyContact) -> int:
        if contact.legacy_id not in BUDDIES:
            raise XMPPError(text="Contact does not exist")

        self.xmpp.ack(msg)
        contact.ack(msg)
        await asyncio.sleep(1)

        contact.displayed(msg)
        await asyncio.sleep(1)

        contact.active()
        # not sure why this does not work in gajim.
        # it seems to work with telegram and signal...
        contact.composing()
        await asyncio.sleep(1)

        legacy_msg_id = self.counter
        reply = contact.send_message("OK", legacy_msg_id=legacy_msg_id)

        # useful to transport "read mark" to the legacy network
        log.debug("Sent message ID: %s", reply["id"])

        async def later():
            await asyncio.sleep(1)
            contact.inactive()

        self.xmpp.loop.create_task(later())

        return legacy_msg_id

    async def active(self, c: LegacyContact):
        log.debug("User is active for contact %s", c)

    async def inactive(self, c: LegacyContact):
        log.debug("User is inactive for contact %s", c)

    async def composing(self, c: LegacyContact):
        log.debug("User is composing for contact %s", c)

    async def displayed(self, legacy_msg_id: int, c: LegacyContact):
        log.debug("Message #%s was read by the user", legacy_msg_id)


class LegacyClient(BaseLegacyClient):
    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        if registration_form["username"] != "n":
            raise LegacyError("Y a que N!")

    async def unregister(self, user: GatewayUser, iq: Iq):
        log.debug("User has unregistered from the gateway", user)


ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"

BUDDIES = ["baba", "bibi"]
AVATARS = []

with (ASSETS_DIR / "buddy1.png").open("rb") as fp:
    AVATARS.append(fp.read())

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())

log = logging.getLogger(__name__)
