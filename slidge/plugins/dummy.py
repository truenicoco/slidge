"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Hashable

from slixmpp import JID, Presence, Iq
from slixmpp.plugins.xep_0100 import LegacyError

from slidge import *


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist.\n"
        "You can use any password you want."
    )
    REGISTRATION_FIELDS = list(BaseGateway.REGISTRATION_FIELDS) + [
        RegistrationField(
            name="something_else",
            label="Some optional stuff not covered by jabber:iq:register",
            required=False,
            private=True,
        )
    ]

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        if registration_form["username"] != "n":
            raise LegacyError("Y a que N!")

    async def unregister(self, user: GatewayUser, iq: Iq):
        log.debug("User has unregistered from the gateway", user)


class Session(BaseSession):
    def __init__(self, user):
        super(Session, self).__init__(user)
        self.counter = 0

    async def login(self, p: Presence):
        for b, a in zip(BUDDIES, AVATARS):
            c = self.contacts.by_legacy_id(b.lower())
            c.name = b.title()
            c.avatar = a
            await c.add_to_roster()
            c.online()

    async def logout(self, p: Presence):
        log.debug("User has disconnected")

    async def send_text(self, t: str, c: LegacyContact):
        i = self.counter
        self.counter = i + 1
        self.xmpp.loop.create_task(self.later(c))
        return i

    async def send_file(self, u: str, c: LegacyContact) -> Optional[Hashable]:
        pass

    async def later(self, c: LegacyContact):
        i = self.counter - 1
        await asyncio.sleep(1)
        c.received(i)
        await asyncio.sleep(1)
        c.ack(i)
        await asyncio.sleep(1)
        c.active()
        await asyncio.sleep(1)
        c.displayed(i)
        await asyncio.sleep(1)
        c.composing()
        await asyncio.sleep(1)
        c.paused()
        await asyncio.sleep(1)
        c.composing()
        await asyncio.sleep(1)
        c.send_text("OK", legacy_msg_id=i)
        await asyncio.sleep(1)
        c.inactive()

    async def active(self, c: LegacyContact):
        log.debug("User is active for contact %s", c)

    async def inactive(self, c: LegacyContact):
        log.debug("User is inactive for contact %s", c)

    async def composing(self, c: LegacyContact):
        log.debug("User is composing for contact %s", c)

    async def displayed(self, legacy_msg_id: int, c: LegacyContact):
        log.debug("Message #%s was read by the user", legacy_msg_id)


ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"

BUDDIES = ["baba", "bibi"]
AVATARS = []

with (ASSETS_DIR / "buddy1.png").open("rb") as fp:
    AVATARS.append(fp.read())

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())

log = logging.getLogger(__name__)
