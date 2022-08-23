"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from slixmpp import JID

from slidge import *

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
            private=False,
        ),
        FormField(
            var="device",
            type="list-single",
            label="What do you want to do?",
            options=[
                {"label": "Choice #1", "value": "choice1"},
                {"label": "Choice #2", "value": "choice2"},
            ],
            required=True,
        ),
    ]

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        if registration_form["username"] != "n":
            raise ValueError("Y a que N!")


class Session(BaseSession[LegacyContact, LegacyRoster, Gateway]):
    def __init__(self, user):
        super(Session, self).__init__(user)
        self.counter = 0
        self.xmpp.loop.create_task(self.backfill())

    async def backfill(self):
        self.log.debug("CARBON")
        i = uuid.uuid1()

        self.contacts.by_legacy_id("bibi").carbon(
            f"Sent by the component on behalf of the user, but this does not seem to reach MAM? {i}",
            legacy_id=i,
        )

    async def paused(self, c: LegacyContact):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def login(self):
        log.debug("Logging in user: %s", self.user)
        self.send_gateway_status("Connecting...", show="dnd")
        await asyncio.sleep(1)
        self.send_gateway_status("Connected")
        for b, a in zip(BUDDIES, AVATARS):
            c = self.contacts.by_legacy_id(b.lower())
            c.name = b.title()
            c.avatar = a
            await c.add_to_roster()
            c.online("I am not a real person, so what?")
        return "You can talk to your fake friends now"

    async def logout(self):
        log.debug("User has disconnected")

    async def send_text(self, t: str, c: LegacyContact, *, reply_to_msg_id=None):
        i = self.counter
        self.counter = i + 1
        self.xmpp.loop.create_task(self.later(c, i))

        if t == "crash":
            raise RuntimeError("PANIC!!!")
        return i

    async def send_file(self, u: str, c: LegacyContact, *, reply_to_msg_id=None) -> int:
        i = self.counter
        self.counter = i + 1
        c.send_text(u)
        await c.send_file(ASSETS_DIR / "buddy1.png")
        return i

    async def later(self, c: LegacyContact, trigger_msg_id: int):
        i = self.counter - 1
        await asyncio.sleep(1)
        c.received(i)
        await asyncio.sleep(1)
        c.active()
        await asyncio.sleep(1)
        c.displayed(i)
        await asyncio.sleep(1)
        c.ack(i)
        await asyncio.sleep(1)
        c.composing()
        await asyncio.sleep(1)
        c.paused()
        await asyncio.sleep(1)
        c.composing()
        await asyncio.sleep(1)
        c.send_text("OK", legacy_msg_id=i, reply_to_msg_id=trigger_msg_id)
        await asyncio.sleep(1)
        i = uuid.uuid1().int
        c.send_text("I will retract this", legacy_msg_id=i)
        c.retract(i)
        c.inactive()

    async def active(self, c: LegacyContact):
        log.debug("User is active for contact %s", c)

    async def inactive(self, c: LegacyContact):
        log.debug("User is inactive for contact %s", c)

    async def composing(self, c: LegacyContact):
        log.debug("User is composing for contact %s", c)

    async def displayed(self, legacy_msg_id: int, c: LegacyContact):
        log.debug("Message #%s was read by the user", legacy_msg_id)

    async def search(self, form_values: dict[str, str]):
        if form_values["first"] == "bubu":
            return SearchResult(
                fields=[
                    FormField("name", label="Name"),
                    FormField("jid", type="jid-single"),
                ],
                items=[{"name": "bubu", "jid": f"bubu@{self.xmpp.boundjid.bare}"}],
            )

    async def react(self, legacy_msg_id, emojis, c):
        c.react(legacy_msg_id, "♥")

    async def retract(self, legacy_msg_id, c):
        log.debug("User has retracted their msg: '%s' (sent to '%s')", legacy_msg_id, c)


BUDDIES = ["baba", "bibi"]
AVATARS = []

with (ASSETS_DIR / "buddy1.png").open("rb") as fp:
    AVATARS.append(fp.read())

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())

log = logging.getLogger(__name__)
