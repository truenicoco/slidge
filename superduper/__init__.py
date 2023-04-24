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

from slidge import (
    BaseGateway,
    BaseSession,
    FormField,
    GatewayUser,
    LegacyBookmarks,
    LegacyContact,
    LegacyMUC,
    LegacyParticipant,
    LegacyRoster,
    MucType,
    SearchResult,
)
from slidge.core.command import Command, CommandAccess, Form
from slidge.core.command.register import RegistrationType

ASSETS_DIR = Path(__file__).parent / "assets"


class Friend(Command):
    NAME = ""
    HELP = ""
    NODE = CHAT_COMMAND = "friend"
    ACCESS = CommandAccess.USER_LOGGED

    async def run(self, session: Optional["BaseSession"], ifrom: JID, *args):
        return Form(
            title="Name of your friend",
            instructions="Give a random name",
            fields=[FormField("name", required=True)],
            handler=self.finish,
        )

    @staticmethod
    async def finish(form_values, session: "Session", _ifrom):
        contact = await session.contacts.by_legacy_id(form_values["name"])
        contact.is_friend = False
        contact.send_friend_request("Voulez-vous être mon ami et devenir riche?")


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part):
        if local_part not in {"prout-1", "prout2"}:
            raise XMPPError("item-not-found")
        return local_part

    async def fill(self):
        for i in range(1, 3):
            muc = await self.by_legacy_id(f"prout-{i}")
            muc.DISCO_NAME = f"A friendly name {i}"


class MUC(LegacyMUC):
    REACTIONS_SINGLE_EMOJI = True

    session: "Session"
    msg_ids = defaultdict(int)  # type: ignore

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.xmpp.loop.create_task(self.later_error())

    async def join(self, p):
        self.user_nick = "SomeNick"
        await super().join(p)

    async def backfill(self, oldest_message_id=None, oldest_date=None):
        part = await self.get_participant("someone")
        # await asyncio.sleep(5)
        for i in range(10, 0, -1):
            log.debug("HISTORY")
            ui = uuid.uuid4()
            part.send_text(
                f"history {i} {ui}",
                f"-{i}-{ui}",
                when=datetime.now() - timedelta(minutes=i),
                archive_only=True,
            )

    async def later_error(self):
        p = await self.get_participant("errorer")
        p.send_text("ERRORING", full_jid="test@localhost/gajim.EF51E8Y")
        # resource does not exist
        # <message type="error" id="3d7fa9d5b1f34dff9eb4f9161a867a01" from="test@localhost/caca"
        # to="prout-1@dummy.localhost/live-messager"><error type="cancel"><service-unavailable
        # xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" /></error></message>
        # resource is not joined
        # <message type="error" id="a116ffc9d046457c850fa79d1ca59886" from="test@localhost/gajim.EF51PE"
        # to="prout-1@dummy.localhost/live-messager"><error type="cancel"><service-unavailable
        # xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" /></error></message>

    async def update_info(self):
        if self.legacy_id == "prout-1":
            self.type = MucType.GROUP
        self.avatar = AVATARS[0]

    async def fill_participants(self):
        if self.legacy_id == "prout-1":
            for nick in "anon1", "anon2":
                await self.get_participant(nick)
            await self.get_participant_by_contact(
                await self.session.contacts.by_legacy_id("bibi")
            )
        elif self.legacy_id == "prout2":
            for nick in "anon1", "anon2", "anon3", "anon4":
                await self.get_participant(nick)

    async def rename_all(self):
        for p in list(await self.get_participants()):
            log.debug("Renaming %s", p)
            if p.contact:
                p.contact.name = "new--" + p.nickname
            elif not p.is_system and not p.is_user:
                p.nickname = "new--anon--" + p.nickname


class Participant(LegacyParticipant):
    pass


class Contact(LegacyContact):
    REACTIONS_SINGLE_EMOJI = False
    RETRACTION = True
    CORRECTION = False

    async def available_emojis(self, legacy_msg_id=None):
        return {"🦅", "🧺"}

    async def update_info(self):
        self.name = self.legacy_id.title()
        try:
            self.avatar = AVATARS[BUDDIES.index(self.legacy_id)]
        except ValueError:
            pass
        self.is_friend = self.name.lower() in BUDDIES
        if self.is_friend:
            await self.add_to_roster()
            await asyncio.sleep(1)
            self.online("I am not a real person, so what?")

    async def on_friend_request(self, text=""):
        self.send_text("Qui es-tu?")
        await asyncio.sleep(5)
        if text == "refuse":
            await self.reject_friend_request("Nope")
        else:
            await self.accept_friend_request("OK let's see")

    async def on_friend_delete(self, text=""):
        self.send_friend_request("Stay my friend, please :(")


class Gateway(BaseGateway):
    COMPONENT_NAME = "The great legacy network (slidge)"
    COMPONENT_AVATAR = ASSETS_DIR / "slidge-color.png"
    COMPONENT_TYPE = "aim"
    GROUPS = True
    REGISTRATION_INSTRUCTIONS = (
        "Only username 'n' is accepted and only 'baba' and 'bibi' contacts exist.\n"
        "You can use any password you want."
    )
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE
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
    async def jid_username_to_legacy_id(self, jid_username: str):
        return jid_username

    async def fill(self):
        for b, a in zip(BUDDIES, AVATARS):
            c = await self.by_legacy_id(b.lower())
            await c.add_to_roster()


class Session(BaseSession):
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
            "You're bad!",
            legacy_msg_id=i,
            when=datetime.now() - timedelta(hours=5),
            carbon=True,
        )
        baba.send_text(
            "You're worse",
            legacy_msg_id=i,
            when=datetime.now() - timedelta(hours=4),
        )

        muc = await self.bookmarks.by_legacy_id("prout-1")
        p = await muc.get_participant("live-messager")
        p.send_text("Live message!", uuid.uuid4())

    async def paused(self, c: LegacyContact, thread=None):
        pass

    async def correct(
        self, c: LegacyContact, text: str, legacy_msg_id: Any, thread=None
    ):
        pass

    async def login(self):
        log.debug("Logging in user: %s", self.user)
        await asyncio.sleep(1)
        return "You can talk to your fake friends now"

    async def logout(self):
        log.debug("User has disconnected")

    async def send_text(
        self,
        chat: Union[LegacyContact, MUC],
        text: str,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to=None,
        thread=None,
    ):
        if isinstance(chat, LegacyContact):
            await chat.send_file(
                ASSETS_DIR / "slidge-mono-black.png",
                file_name="slidge-mono-black.jpg",
                caption="This is a caption",
            )

        if isinstance(chat, MUC):
            self.xmpp.loop.create_task(chat.rename_all())
            return str(uuid.uuid4())

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
        elif text == "invite":
            self.send_gateway_invite(
                await self.bookmarks.by_legacy_id("prout-1"), reason="Because why not?"
            )
        elif text == "bookmarks":
            muc = await self.bookmarks.by_legacy_id("prout-1")
            await muc.add_to_bookmarks(auto_join=True)
        else:
            self.xmpp.loop.create_task(self.later(chat, i, body=text))

        return i

    async def crash(self):
        raise RuntimeError("PANIC222!!!")

    async def send_file(
        self, chat: Union[LegacyContact, MUC], url: str, *_, **__
    ) -> int:
        i = self.counter
        self.counter = i + 1
        if isinstance(chat, MUC):
            replier = await chat.get_participant("uploader")
        else:
            replier = chat  # type: ignore
        replier.send_text(url)
        await replier.send_file(
            ASSETS_DIR / "slidge-mono-black.png", caption="This is a caption"
        )
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

    async def active(self, c: LegacyContact, thread=None):
        log.debug("User is active for contact %s", c)

    async def inactive(self, c: LegacyContact, thread=None):
        log.debug("User is inactive for contact %s", c)

    async def composing(self, c: LegacyContact, thread=None):
        log.debug("User is composing for contact %s", c)

    async def displayed(self, c: LegacyContact, legacy_msg_id: int, thread=None):
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

    async def react(self, c, legacy_msg_id, emojis, thread=None):
        if "😈" in emojis:
            c.send_text("That's forbidden")
            c.react(legacy_msg_id, "", carbon=True)
            raise XMPPError("not-acceptable")
        else:
            c.react(legacy_msg_id, "♥")

    async def retract(self, c, legacy_msg_id, thread=None):
        log.debug("User has retracted their msg: '%s' (sent to '%s')", legacy_msg_id, c)


BUDDIES = ["baba", "bibi"]
AVATARS = ["https://wallpapercave.com/wp/PSksftM.jpg"]

with (ASSETS_DIR / "slidge-mono-white.png").open("rb") as fp:
    AVATARS.append(fp.read())  # type:ignore

log = logging.getLogger(__name__)
