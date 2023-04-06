import asyncio
from typing import TYPE_CHECKING, Optional, Union, cast

import discord as di

from slidge import BaseSession, XMPPError

if TYPE_CHECKING:
    from .contact import Contact, Roster
    from .group import MUC

Recipient = Union["MUC", "Contact"]


class Session(BaseSession[int, Recipient]):
    contacts: "Roster"

    def __init__(self, user):
        super().__init__(user)
        from .client import Discord

        self.discord = Discord(self)
        self.send_lock = asyncio.Lock()

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str):
        return int(i)

    async def login(self):
        token = self.user.registration_form["token"]
        assert isinstance(token, str)
        await self.discord.login(token)
        self.xmpp.loop.create_task(self.discord.connect())

        await self.discord.wait_until_ready()
        assert self.discord.user is not None
        self.contacts.user_legacy_id = self.discord.user.id
        self.bookmarks.user_nick = str(self.discord.user.display_name)
        return f"Logged on as {self.discord.user}"

    async def send_text(
        self,
        chat: Recipient,
        text: str,
        reply_to_msg_id=None,
        thread=None,
        **kwargs,
    ):
        recipient = await get_recipient(chat, thread)
        if reply_to_msg_id is None:
            reference = None
        else:
            reference = di.MessageReference(
                message_id=reply_to_msg_id, channel_id=recipient.id
            )

        async with self.send_lock:
            msg = await recipient.send(text, reference=reference)  # type:ignore
        mid = msg.id
        self.discord.ignore_next_msg_event.add(mid)
        return mid

    async def logout(self):
        await self.discord.close()

    async def send_file(self, chat: Recipient, url: str, thread=None, **kwargs):
        # discord clients inline previews of external URLs, so no need to actually send on discord servers
        recipient = await get_recipient(chat, thread)
        await recipient.send(url)

    async def active(self, c: Recipient, thread=None):
        pass

    async def inactive(self, c: Recipient, thread=None):
        pass

    async def composing(self, c: Recipient, thread=None):
        recipient = await get_recipient(c, thread)
        async with recipient.typing():
            await asyncio.sleep(5)

    async def paused(self, c: Recipient, thread=None):
        pass

    async def displayed(self, c: Recipient, legacy_msg_id: int, thread=None):
        if not isinstance(legacy_msg_id, int):
            self.log.debug("This is not a valid discord msg id: %s", legacy_msg_id)
            return

        recipient = await get_recipient(c, thread)
        try:
            m = await recipient.fetch_message(legacy_msg_id)
        except di.errors.NotFound:
            return

        try:
            await m.ack()  # triggers 404, maybe does not work for DM?
        except Exception as e:
            self.log.exception(
                "Message %s should have been marked as read but this raised %s", m, e
            )

    async def correct(self, c: Recipient, text: str, legacy_msg_id: int, thread=None):
        channel = await get_recipient(c, thread)
        self.discord.ignore_next_msg_event.add(legacy_msg_id)
        m = await channel.fetch_message(legacy_msg_id)
        await m.edit(content=text)

    async def react(
        self, c: Recipient, legacy_msg_id: int, emojis: list[str], thread=None
    ):
        channel = await get_recipient(c, thread)

        m = await channel.fetch_message(legacy_msg_id)

        legacy_reactions = set(self.get_my_legacy_reactions(m))
        xmpp_reactions = set(emojis)

        self.log.debug("%s vs %s", legacy_reactions, xmpp_reactions)
        for e in xmpp_reactions - legacy_reactions:
            await m.add_reaction(e)
        for e in legacy_reactions - xmpp_reactions:
            await m.remove_reaction(e, self.discord.user)  # type:ignore

    async def retract(self, c: Recipient, legacy_msg_id: int, thread=None):
        channel = await get_recipient(c, thread)
        self.discord.ignore_next_msg_event.add(legacy_msg_id)
        m = await channel.fetch_message(legacy_msg_id)
        await m.delete()

    async def update_reactions(self, message: di.Message):
        if isinstance(message.channel, di.DMChannel):
            me = await self.contacts.by_discord_user(message.channel.recipient)
        elif isinstance(message.channel, di.TextChannel):
            muc = await self.bookmarks.by_legacy_id(message.channel.id)
            me = await muc.get_user_participant()
        else:
            self.log.warning("Cannot update reactions for %s", message)
            return
        me.react(message.id, self.get_my_legacy_reactions(message), carbon=True)

    @staticmethod
    def get_my_legacy_reactions(message: di.Message) -> list[str]:
        reactions = []
        for r in message.reactions:
            if r.me and not r.is_custom_emoji():
                assert isinstance(r.emoji, str)
                reactions.append(r.emoji)

        return reactions

    async def search(self, form_values: dict[str, str]):
        pass


async def get_recipient(
    chat: Recipient, thread: Optional[int]
) -> Union[di.DMChannel, di.TextChannel, di.Thread]:
    if chat.is_group:
        chat = cast("MUC", chat)
        channel = await chat.get_discord_channel()
        if thread:
            discord_thread = channel.get_thread(thread)
            if discord_thread is not None:
                return discord_thread
        return channel
    else:
        chat = cast("Contact", chat)
        dm = chat.discord_user.dm_channel
        if dm is None:
            raise XMPPError(
                "recipient-unavailable", "Could not find the associated DM channel"
            )
        return dm
