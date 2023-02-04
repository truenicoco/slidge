from typing import TYPE_CHECKING, Union

import discord as di
from discord.threads import Thread

if TYPE_CHECKING:
    from . import Contact
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
        if isinstance(channel, di.VoiceChannel):
            return

        if isinstance(channel, di.GroupChannel):
            return

        if isinstance(channel, Thread):
            return

        # types: TextChannel, VoiceChannel, Thread, DMChannel, PartialMessageable, GroupChannel

        if (author := message.author) == self.user:
            return await self.on_carbon(message)

        if isinstance(channel, di.DMChannel):
            contact = await self.get_contact(author)
            return await contact.send_message(message)

        if isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(channel.id)
            participant = await muc.get_participant_by_contact(
                await self.get_contact(author)
            )

            return await participant.send_message(message)

    async def on_carbon(self, message: di.Message):
        assert isinstance(message.channel, (di.DMChannel, di.TextChannel))

        async with self.session.send_lock:
            fut = self.session.send_futures.get(message.id)

        if fut is None:
            if isinstance(message.channel, di.DMChannel):
                contact = await self.get_contact(message.channel.recipient)
                contact.send_text(
                    message.content, legacy_msg_id=message.id, carbon=True
                )
            elif isinstance(message.channel, di.TextChannel):
                muc = await self.session.bookmarks.by_legacy_id(message.channel.id)
                participant = await muc.get_user_participant()
                participant.send_text(
                    message.content, legacy_msg_id=message.id, carbon=True
                )
            else:
                self.log.warning("Ignoring carbon? %s", message)
        else:
            fut.set_result(True)

    async def on_typing(self, channel, user, _when):
        if user == self.user:
            return

        contact = await self.get_contact(user)

        if isinstance(channel, di.DMChannel):
            return contact.composing()

        if isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(channel.id)
            part = await muc.get_participant_by_contact(contact)
            return part.composing()

    async def on_message_edit(self, before: di.Message, after: di.Message):
        channel = after.channel

        if isinstance(channel, di.DMChannel):
            correcter = await self.get_contact(channel.recipient)
            if after.author == self.user:
                return await self.on_carbon_edit(before, after, correcter)

        elif isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(after.channel.id)
            if after.author.id == self.user.id:  # type:ignore
                correcter = await muc.get_user_participant()
            else:
                contact = await self.get_contact(after.author)
                correcter = await muc.get_participant_by_contact(contact)

        else:
            self.log.debug("Ignoring edit in: %s", after.channel)
            return

        correcter.correct(after.id, after.content)

    async def on_carbon_edit(
        self, before: di.Message, after: di.Message, contact: "Contact"
    ):
        fut = self.session.edit_futures.get(after.id)
        if fut is None:
            return contact.correct(before.id, after.content, carbon=True)
        fut.set_result(True)

    async def on_message_delete(self, m: di.Message):
        channel = m.channel
        if isinstance(channel, di.DMChannel):
            deleter = await self.get_contact(channel.recipient)

            if m.author == self.user:
                fut = self.session.delete_futures.get(m.id)
                if fut is None:
                    return deleter.retract(m.id, carbon=True)
                return fut.set_result(True)

            deleter.retract(m.id)
        elif isinstance(channel, di.TextChannel):
            contact = await self.get_contact(m.author)
            muc = await self.session.bookmarks.by_legacy_id(m.channel.id)
            deleter = await muc.get_participant_by_contact(contact)
        else:
            self.log.debug("Ignoring delete in: %s", channel)
            return

        deleter.retract(m.id)

    async def on_reaction_add(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser]
    ):
        await self.update_reactions(reaction, user)

    async def on_reaction_remove(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser]
    ):
        await self.update_reactions(reaction, user)

    async def update_reactions(
        self, reaction: di.Reaction, user: Union[di.User, di.ClientUser, di.Member]
    ):
        message = reaction.message
        channel = message.channel

        if isinstance(message.channel, di.DMChannel):
            if isinstance(user, di.ClientUser):
                await self.session.update_reactions(message)
            else:
                contact = await self.get_contact(user)
                await contact.update_reactions(message)

        elif isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(channel.id)

            if user.id == self.user.id:  # type:ignore
                self.log.debug("ME: %s %s", user, type(user))
                participant = await muc.get_user_participant()
            else:
                self.log.debug("NOT ME: %s %s", user, type(user))
                participant = await muc.get_participant_by_contact(
                    await self.session.contacts.by_legacy_id(user.id)
                )

            await participant.update_reactions(message)

    async def get_contact(self, user: Union[di.User, di.Member]):
        return await self.session.contacts.by_discord_user(user)
