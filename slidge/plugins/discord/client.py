from typing import TYPE_CHECKING, Union

import discord as di
from discord.threads import Thread

if TYPE_CHECKING:
    from .session import Session


class Discord(di.Client):
    def __init__(self, session: "Session"):
        super().__init__()
        self.session = session
        self.log = session.log

    async def on_ready(self):
        if (f := self.session.ready_future).done():
            return
        f.set_result(True)
        self.log.debug(f"Logged on as {self.user}")

    async def on_message(self, message: di.Message):
        channel = message.channel
        if isinstance(channel, di.DMChannel):
            return await self.on_message_dm_channel(message)

        if isinstance(channel, di.VoiceChannel):
            return

        if isinstance(channel, di.GroupChannel):
            return

        if isinstance(channel, Thread):
            return

    async def on_message_dm_channel(self, message: di.Message):
        assert isinstance(message.channel, di.DMChannel)
        if (author := message.author) == self.user:
            return await self.on_carbon_dm_channel(message)

        contact = await self.session.contacts.by_discord_user(author)
        reply_to = message.reference.message_id if message.reference else None

        text = message.content
        attachments = message.attachments
        msg_id = message.id

        if not attachments:
            return contact.send_text(
                text, legacy_msg_id=msg_id, reply_to_msg_id=reply_to
            )

        last_attachment_i = len(attachments := message.attachments) - 1
        for i, attachment in enumerate(attachments):
            last = i == last_attachment_i
            await contact.send_file(
                file_url=attachment.url,
                file_name=attachment.filename,
                content_type=attachment.content_type,
                reply_to_msg_id=reply_to if last else None,
                legacy_msg_id=msg_id if last else None,
                caption=text if last else None,
            )

    async def on_carbon_dm_channel(self, message: di.Message):
        assert isinstance(message.channel, di.DMChannel)
        async with self.session.send_lock:
            fut = self.session.send_futures.get(message.id)
        if fut is None:
            (
                await self.session.contacts.by_discord_user(message.channel.recipient)
            ).send_text(message.content, legacy_msg_id=message.id, carbon=True)
        else:
            fut.set_result(True)
        return

    async def on_typing(self, channel, user, _when):
        if user != self.user and isinstance(channel, di.DMChannel):
            (await self.session.contacts.by_discord_user(user)).composing()

    async def on_message_edit(self, before: di.Message, after: di.Message):
        if not isinstance(after.channel, di.DMChannel):
            return
        if before.content == after.content:
            return
        if (author := after.author) == self.user:
            fut = self.session.edit_futures.get(after.id)
            if fut is None:
                (
                    await self.session.contacts.by_discord_user(after.channel.recipient)
                ).correct(before.id, after.content, carbon=True)
            else:
                fut.set_result(True)
        else:
            (await self.session.contacts.by_discord_user(author)).correct(
                after.id, after.content
            )

    async def on_message_delete(self, m: di.Message):
        if not isinstance(m.channel, di.DMChannel):
            return
        if (author := m.author) == self.user:
            fut = self.session.delete_futures.get(m.id)
            if fut is None:
                (
                    await self.session.contacts.by_discord_user(m.channel.recipient)
                ).retract(m.id, carbon=True)
            else:
                fut.set_result(True)
        else:
            (await self.session.contacts.by_discord_user(author)).retract(m.id)

    async def on_reaction_add(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser]
    ):
        await self.update_reactions(reaction, user)

    async def on_reaction_remove(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser]
    ):
        await self.update_reactions(reaction, user)

    async def update_reactions(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser]
    ):
        message: di.Message = reaction.message
        if not isinstance(message.channel, di.DMChannel):
            return

        if isinstance(user, di.ClientUser):
            await self.session.update_reactions(message)
        else:
            await (await self.session.contacts.by_discord_user(user)).update_reactions(
                message
            )
