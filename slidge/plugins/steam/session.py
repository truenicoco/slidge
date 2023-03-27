import asyncio
from typing import Optional, Union

from slidge import BaseSession, LegacyMUC, XMPPError

from .client import SteamClient
from .contact import Contact, Roster
from .util import EMOJIS

Recipient = Union[Contact, LegacyMUC]


class Session(BaseSession[int, Recipient]):
    contacts: "Roster"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.steam = SteamClient(self)
        self.login_task: Optional[asyncio.Task] = None

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        return int(i)

    async def login(self):
        self.login_task = asyncio.create_task(self.steam.login_from_token())
        await self.steam.wait_until_ready()

    async def active(self, c, thread=None):
        pass

    async def composing(self, c: Recipient, *_, **__):
        if c.is_group:
            return
        assert isinstance(c, Contact)
        user = await c.get_user()
        await user.typing()

    async def send_text(self, chat: Recipient, text: str, **_kwargs):
        if not isinstance(chat, Contact):
            raise XMPPError(
                "feature-not-implemented", "Group chats are not supported yet"
            )
        user = await chat.get_user()
        message = await user.send(text)
        self.steam.waiting_for_acks.add(message.id)
        return message.id

    async def send_file(self, chat: Recipient, url: str, **_kwargs):
        if not isinstance(chat, Contact):
            raise XMPPError(
                "feature-not-implemented", "Group chats are not supported yet"
            )
        user = await chat.get_user()
        # TODO: use send(media=...)
        message = await user.send(url)
        return message.id

    async def react(
        self, chat: Recipient, legacy_msg_id: int, emojis: list[str], thread=None
    ):
        if not isinstance(chat, Contact):
            raise XMPPError(
                "feature-not-implemented", "Group chats are not supported yet"
            )
        user = await chat.get_user()
        self.log.debug("FETCHING")
        msg = await user.fetch_message(legacy_msg_id)
        self.log.debug("MESSAGE: %s", msg)

        reactions_xmpp = set(emojis)
        reactions_steam = set()
        for r in msg.reactions:
            if emoticon := r.emoticon:
                if r.user == self.steam.user:
                    reactions_steam.add(EMOJIS.get(emoticon.name, "❔"))

        to_remove = reactions_steam - reactions_xmpp
        to_add = reactions_xmpp - reactions_steam

        emoticons = {e.name: e for e in self.steam.emoticons}
        self.log.debug("emoticons: %s", emoticons)

        for emoji in to_add:
            emoticon_name = EMOJIS.inverse.get(emoji)
            if emoticon_name is None:
                raise XMPPError("bad-request", f"Forbidden emoji: {emoji}")
            emoticon = emoticons[emoticon_name]
            self.log.debug("msg: %s", msg)
            self.log.debug("emoticon: %r", emoticon)
            await msg.add_emoticon(emoticon)
        #
        for emoji in to_remove:
            emoticon_name = EMOJIS.inverse[emoji]
            emoticon = emoticons[emoticon_name]
            await msg.remove_emoticon(emoticon)

    async def correct(
        self, chat: Recipient, text: str, legacy_msg_id: int, thread=None
    ):
        raise XMPPError("feature-not-implemented", "No correction in steam")

    async def retract(self, chat: Recipient, legacy_msg_id: int, thread=None):
        raise XMPPError("feature-not-implemented", "No retraction in steam")
