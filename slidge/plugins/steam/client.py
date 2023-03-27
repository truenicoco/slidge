import logging
from asyncio import Future, get_running_loop
from datetime import datetime
from typing import TYPE_CHECKING

import steam

from slidge import global_config

from .util import EMOJIS

if TYPE_CHECKING:
    from .session import Session


class Base(steam.Client):
    user_jid: str

    def __init__(self, *a, **k):
        super().__init__(*a, **k)

    def _get_store(self):
        return global_config.HOME_DIR / self.user_jid

    def save_token(self):
        self._get_store().write_text(self.refresh_token)


class CredentialsValidation(Base):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.code_future: Future[str] = get_running_loop().create_future()

    async def code(self):
        return await self.code_future


class SteamClient(Base):
    def __init__(self, session: "Session", *a, **k):
        self.session = session
        self.user_jid = session.user.bare_jid
        self.waiting_for_acks = set[int]()
        super().__init__(*a, **k)

    async def login_from_token(self):
        return await self.login(refresh_token=self._get_store().read_text())

    async def code(self):
        return await self.session.input(
            "You have been disconnected, please enter the code "
            "you received via email or steam guard"
        )

    async def on_typing(self, user: steam.User, when: datetime):
        c = await self.session.contacts.by_steam_user(user)
        c.composing()

    async def on_message(self, message: steam.Message):
        if not isinstance(message, steam.UserMessage):
            return
        if message.id in self.waiting_for_acks:
            self.waiting_for_acks.remove(message.id)
            return
        c = await self.session.contacts.by_steam_user(message.channel.participant)
        c.send_text(
            message.clean_content, message.id, carbon=message.author == self.user
        )
        await message.add_emoticon(self.emoticons[0])

    async def on_reaction_add(self, reaction: steam.MessageReaction):
        await self.update_reactions(reaction, add=True)

    async def on_reaction_remove(self, reaction: steam.MessageReaction):
        await self.update_reactions(reaction, add=False)

    async def update_reactions(self, reaction: steam.MessageReaction, add: bool):
        message = reaction.message
        if not isinstance(message, steam.UserMessage):
            return
        if not reaction.emoticon:
            return

        assert isinstance(message.channel, steam.UserChannel)
        c = await self.session.contacts.by_steam_user(message.channel.participant)
        user_reactions = set()
        contact_reactions = set()
        self.session.log.debug("reactions: %s", message.reactions)
        for r in message.reactions:
            self.session.log.debug("reaction: %s", r)
            if emoticon := r.emoticon:
                emoji = EMOJIS.get(emoticon.name, "❔")
                if r.user == self.user:
                    user_reactions.add(emoji)
                else:
                    contact_reactions.add(emoji)

        if reaction.user == self.user:
            if add:
                user_reactions.add(reaction.emoticon.name)
            else:
                user_reactions.remove(reaction.emoticon.name)
        else:
            if add:
                contact_reactions.add(reaction.emoticon.name)
            else:
                contact_reactions.remove(reaction.emoticon.name)

        c.react(message.id, contact_reactions)
        c.react(message.id, user_reactions, carbon=True)

    async def on_user_update(self, before: steam.User, after: steam.User):
        log.debug("user update: %s %s", before, after)
        c = await self.session.contacts.by_steam_user(after)
        c.update_info(after)


log = logging.getLogger(__name__)
