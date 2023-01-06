"""
A pseudo legacy network, to easily test things
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Union

from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *
from slidge.core.adhoc import RegistrationType

ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part):
        if local_part not in {"prout-1", "prout2"}:
            raise XMPPError("not-found")
        return local_part


class MUC(LegacyMUC["Session", str, "Participant", str]):
    REACTIONS_SINGLE_EMOJI = True

    session: "Session"
    msg_ids = defaultdict(int)  # type: ignore

    async def join(self, p):
        self.user_nick = "SomeNick"
        await super().join(p)

    async def fill_history(
        self,
        full_jid: JID,
        maxchars: Optional[int] = None,
        maxstanzas: Optional[int] = None,
        seconds: Optional[int] = None,
        since: Optional[int] = None,
    ):
        if maxchars is not None and maxchars == 0:
            return
        part = await self.get_participant("someone")
        log.debug("PART")
        for i in range(10, 0, -1):
            log.debug("HISTORY")
            part.send_text(
                "history",
                f"-{i}",
                when=datetime.now() - timedelta(hours=i),
                full_jid=full_jid,
            )

    async def get_participants(self):
        if self.legacy_id == "prout-1":
            for nick in "anon1", "anon2":
                yield Participant(self, nick)
                break
        elif self.legacy_id == "prout2":
            for nick in "anon1", "anon2", "anon3", "anon4":
                yield Participant(self, nick)

    async def send_text(self, text: str) -> str:
        self.msg_ids[self.legacy_id] += 1
        i = self.msg_ids[self.legacy_id]
        self.xmpp.loop.create_task(self.session.muc_later(self, text, i))
        return str(self.msg_ids[self.legacy_id])


class Participant(LegacyParticipant[MUC]):
    pass


class Contact(LegacyContact):
    REACTIONS_SINGLE_EMOJI = True

    async def available_emojis(self, legacy_msg_id):
        return {"🦅", "🧺"}


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    COMPONENT_AVATAR = ASSETS_DIR / "gateway.png"
    COMPONENT_TYPE = "aim"
    GROUPS = True
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist.\n"
        "You can use any password you want."
    )
    REGISTRATION_TYPE = RegistrationType.QRCODE
    MARK_ALL_MESSAGES = True

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        if registration_form["username"] != "n":
            raise XMPPError("bad-request", "Y a que N!")

    async def validate_two_factor_code(self, user, code):
        if code != "8":
            raise XMPPError("not-authorized", text="Wrong code! It's 8.")

    async def get_qr_text(self, user: GatewayUser) -> str:
        self.loop.create_task(self.later_confirm_qr(user))
        return "dummy:///SLIDGE-IS-GREAT-AGAIN/prout"

    async def later_confirm_qr(self, user: GatewayUser):
        await asyncio.sleep(1)
        exc = (
            XMPPError("bad-request", "Ben non")
            if user.registration_form["password"] == "n"
            else None
        )
        await self.confirm_qr(user.bare_jid, exc)


class Roster(LegacyRoster):
    @staticmethod
    async def jid_username_to_legacy_id(jid_username: str):
        if jid_username not in BUDDIES + ["bubu"]:
            raise XMPPError("not-found")
        return jid_username


class Session(
    BaseSession[
        Gateway, int, LegacyRoster, LegacyContact, LegacyBookmarks, MUC, Participant
    ]
):
    def __init__(self, user):
        super(Session, self).__init__(user)
        self.counter = 0
        self.xmpp.loop.create_task(self.backfill())
        self.xmpp.loop.create_task(
            self.contacts.by_legacy_id("bibi")
        ).add_done_callback(
            lambda c: c.result().set_vcard(
                given="FirstBi",
                surname="LastBi",
                phone="+555",
                full_name="Bi bi",
                note="A fake friend, always there for you",
                url="https://example.org",
                email="bibi@prout.com",
                country="Westeros",
                locality="The place with the thing",
            )
        )
        self.xmpp.loop.create_task(self.add_groups())

    async def add_groups(self):
        muc = await self.bookmarks.by_legacy_id("prout-1")
        muc.n_participants = 45
        await self.bookmarks.by_legacy_id("prout2")
        muc.n_participants = 885

    async def muc_later(self, muc: MUC, text: str, trigger_msg_id: int):
        replier = await muc.get_participant("anon1")
        log.debug("REPLIER: %s", replier)
        await asyncio.sleep(0.5)
        replier.composing()
        await asyncio.sleep(0.5)
        replier.send_text("prout", trigger_msg_id * 1000)
        # next(muc.participants).send_text("I agree. Ain't that great?")

    async def backfill(self):
        self.log.debug("CARBON")
        i = uuid.uuid1()

        baba = await self.contacts.by_legacy_id("baba")

        baba.send_text(
            f"You're bad!",
            legacy_msg_id=i,
            when=datetime.now() - timedelta(hours=5),
            carbon=True,
        )
        baba.send_text(
            f"You're worse",
            legacy_msg_id=i,
            when=datetime.now() - timedelta(hours=4),
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
            c = await self.contacts.by_legacy_id(b.lower())
            c.name = b.title()
            c.avatar = a
            await c.add_to_roster()
            c.online("I am not a real person, so what?")
        return "You can talk to your fake friends now"

    async def logout(self):
        log.debug("User has disconnected")

    async def send_text(
        self,
        text: str,
        chat: Union[LegacyContact, MUC],
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to=None,
    ):
        if isinstance(chat, MUC):
            await chat.send_text(text)
            return

        log.debug("REPLY FALLBACK: %r", reply_to_fallback_text)
        i = self.counter
        self.counter = i + 1

        if text == "crash":
            raise RuntimeError("PANIC!!!")
        if text == "crash2":
            self.xmpp.loop.create_task(self.crash())
        elif text == "delete":
            self.xmpp.loop.create_task(self.later_carbon_delete(chat, i))
        elif text == "nick":
            chat.name = "NEWNAME"
        elif text == "avatar":
            chat.avatar = ASSETS_DIR / "5x5.png"
        elif text == "nonick":
            chat.name = None
        else:
            self.xmpp.loop.create_task(self.later(chat, i, body=text))

        return i

    async def crash(self):
        raise RuntimeError("PANIC222!!!")

    async def send_file(self, url: str, chat: Union[LegacyContact, MUC], **k) -> int:
        i = self.counter
        self.counter = i + 1
        if isinstance(chat, MUC):
            replier = await chat.get_participant("uploader")
        else:
            replier = chat  # type: ignore
        replier.send_text(url)
        await replier.send_file(ASSETS_DIR / "buddy1.png", caption="This is a caption")
        return i

    async def later(self, c: LegacyContact, trigger_msg_id: int, body: str):
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
        c.send_text(
            "OK",
            legacy_msg_id=i,
            reply_to_msg_id=trigger_msg_id,
            reply_to_fallback_text=body,
        )
        await asyncio.sleep(1)
        i = uuid.uuid1().int
        c.send_text("I will retract this", legacy_msg_id=i)
        c.retract(i)
        c.inactive()

    async def later_carbon_delete(self, c: LegacyContact, trigger_msg_id: int):
        await asyncio.sleep(1)
        c.retract(trigger_msg_id, carbon=True)

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
        if "😈" in emojis:
            c.send_text("That's forbidden")
            c.react(legacy_msg_id, "", carbon=True)
            raise XMPPError("not-acceptable")
        else:
            c.react(legacy_msg_id, "♥")

    async def retract(self, legacy_msg_id, c):
        log.debug("User has retracted their msg: '%s' (sent to '%s')", legacy_msg_id, c)


BUDDIES = ["baba", "bibi"]
AVATARS = ["https://wallpapercave.com/wp/PSksftM.jpg"]

with (ASSETS_DIR / "buddy2.png").open("rb") as fp:
    AVATARS.append(fp.read())  # type:ignore

log = logging.getLogger(__name__)
