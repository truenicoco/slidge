"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Hashable, Any

from slixmpp import JID, Presence

from slidge import *
from slidge.legacy.contact import LegacyContactType

ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    COMPONENT_AVATAR = ASSETS_DIR / "gateway.png"
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist.\n"
        "You can use any password you want."
    )
    REGISTRATION_FIELDS = list(BaseGateway.REGISTRATION_FIELDS) + [
        FormField(
            var="something_else",
            label="Some optional stuff not covered by jabber:iq:register",
            required=False,
            private=True,
        )
    ]

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        if registration_form["username"] != "n":
            raise ValueError("Y a que N!")


class Session(BaseSession):
    def __init__(self, user):
        super(Session, self).__init__(user)
        self.counter = 0

    async def paused(self, c: LegacyContactType):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        pass

    async def login(self, p: Presence):
        log.debug("Logging in user: %s", p)
        self.send_gateway_status("Connecting...", show="dnd")
        await asyncio.sleep(1)
        self.send_gateway_status("Connected")
        for b, a in zip(BUDDIES, AVATARS):
            c = self.contacts.by_legacy_id(b.lower())
            c.name = b.title()
            c.avatar = a
            await c.add_to_roster()
            c.online()

    async def logout(self, p: Optional[Presence]):
        log.debug("User has disconnected")

    async def send_text(self, t: str, c: LegacyContact):
        i = self.counter
        self.counter = i + 1
        self.xmpp.loop.create_task(self.later(c))
        return i

    async def send_file(self, u: str, c: LegacyContact) -> Optional[Hashable]:
        i = self.counter
        self.counter = i + 1
        c.send_text(u)
        await c.send_file(ASSETS_DIR / "buddy1.png")
        return i

    async def later(self, c: LegacyContact):
        i = self.counter - 1
        await asyncio.sleep(1)
        c.received(i)
        await asyncio.sleep(1)
        c.active()
        await asyncio.sleep(1)
        c.ack(i)
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

    async def search(self, form_values: Dict[str, str]):
        if form_values["first"] == "bubu":
            return SearchResult(
                fields=[FormField("first"), FormField("jid", type="jid-single")],
                items=[{"first": "bubu", "jid": f"bubu@{self.xmpp.boundjid.bare}"}],
            )


BUDDIES = ["baba", "bibi"]
AVATARS = []

with (ASSETS_DIR / "buddy1.png").open("rb") as fp:
    AVATARS.append(fp.read())

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())

log = logging.getLogger(__name__)
