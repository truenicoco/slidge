import asyncio
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from slixmpp.exceptions import XMPPError

from slidge.util.types import Hat, MessageReference

from .util import ASSETS_DIR, later

if TYPE_CHECKING:
    from .session import Session


@dataclass
class DirectMessage:
    sender: int
    text: str
    id: str
    reply_to: Optional[str] = None


@dataclass
class GroupMessage:
    sender: int
    group_id: str
    text: str
    id: str
    reply_to: Optional[str] = None


@dataclass
class Profile:
    nickname: str
    full_name: str
    avatar: Path
    avatar_unique_id: str


@dataclass
class GroupInfo:
    name: str
    avatar: Optional[Path] = None
    avatar_unique_id: Optional[str] = None


class SuperDuperClient:
    @staticmethod
    async def send_2fa(username, _password):
        if username != "slidger":
            raise XMPPError("not-authorized", "Use 'slidger' as the username or GTFO")

    @staticmethod
    async def validate_2fa(_username, _password, code: str):
        if code != "666":
            raise XMPPError("not-authorized", "Wrong code! It's 666.")

    def __init__(self, session: "Session"):
        self.session = session
        self.user_id = -1
        self.__task = asyncio.create_task(self.__incoming_messages())

    async def __incoming_messages(self):
        while True:
            await self.on_contact_message(
                DirectMessage(666, "Hey devil!", id=uuid.uuid4().hex)
            )
            await self.on_contact_message(
                DirectMessage(111, "Hey!", id=uuid.uuid4().hex)
            )
            await asyncio.sleep(300)
            await self.on_contact_message(
                DirectMessage(222, "Ho!", id=uuid.uuid4().hex)
            )
            await asyncio.sleep(300)

    async def login(self):
        pass

    async def send_direct_msg(self, text: str, contact_id: int):
        i = uuid.uuid4().hex
        later(
            self.on_contact_message(
                DirectMessage(
                    id=uuid.uuid4().hex, text="A reply", sender=contact_id, reply_to=i
                )
            )
        )
        return DirectMessage(text=text, id=i, sender=self.user_id)

    async def send_group_msg(self, text: str, group_id: str):
        i = uuid.uuid4().hex
        later(
            self.on_group_message(
                GroupMessage(
                    id=uuid.uuid4().hex,
                    text="A reply",
                    group_id=group_id,
                    sender=666,
                    reply_to=i,
                )
            )
        )
        return GroupMessage(text=text, id=i, sender=self.user_id, group_id=group_id)

    @staticmethod
    async def get_profile(user_id: int) -> Profile:
        return _PROFILES[user_id]

    @staticmethod
    async def get_group_info(group_id: str) -> GroupInfo:
        return _GROUPS[group_id]

    async def on_contact_message(self, msg: DirectMessage):
        contact = await self.session.contacts.by_legacy_id(msg.sender)
        contact.send_text(
            msg.text,
            msg.id,
            reply_to=MessageReference(msg.reply_to, author="user"),
        )

    async def on_group_message(self, msg: GroupMessage):
        muc = await self.session.bookmarks.by_legacy_id(msg.group_id)
        participant = await muc.get_participant_by_legacy_id(0)
        participant.send_text(
            msg.text,
            msg.id,
            reply_to=MessageReference(msg.reply_to, author="user"),
        )
        await asyncio.sleep(1)
        participant.send_text(
            msg.text + " (correction)",
            msg.id,
            reply_to=MessageReference(msg.reply_to, author="user"),
            correction=True,
        )
        user = await muc.get_user_participant()
        if random.random() < 0.5:
            user.set_hats([("prout", "prout")])
            participant.set_hats([])
        else:
            user.set_hats([])
            participant.set_hats(
                [
                    Hat("12", "gloup"),
                    Hat(
                        str(random.randint(0, 256)),
                        "gloup" + str(random.randint(0, 256)),
                    ),
                ]
            )


_PROFILES = {
    111: Profile(
        nickname="Baba",
        avatar=ASSETS_DIR / "slidge-mono-black.png",
        full_name="Ba Ba",
        avatar_unique_id="baba-uid",
    ),
    222: Profile(
        nickname="Bibi",
        avatar=ASSETS_DIR / "slidge-mono-white.png",
        full_name="Bi Bi",
        avatar_unique_id="bibi-uid",
    ),
    666: Profile(
        nickname="The devil",
        avatar=ASSETS_DIR / "5x5.png",
        full_name="Lucy Fer",
        avatar_unique_id="devil-uid",
    ),
    000: Profile(
        nickname="🎉 The joker 🎉",
        avatar=ASSETS_DIR / "5x5.png",
        full_name="A guy with emojis in his nick",
        avatar_unique_id="devil-uid",
    ),
}

_GROUPS = {
    "aaa": GroupInfo(
        name="The groupchat A",
        avatar=ASSETS_DIR / "slidge-color-small.png",
        avatar_unique_id="slidge-color-small",
    ),
    "bbb": GroupInfo(
        name="The groupchat B",
    ),
}
