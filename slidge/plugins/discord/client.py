from typing import TYPE_CHECKING, Union

import discord as di

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Discord(di.Client):
    def __init__(self, session: "Session"):
        super().__init__()
        self.session = session
        self.log = session.log

    async def on_message(self, message: di.Message):
        channel = message.channel
        if isinstance(channel, di.VoiceChannel):
            return

        if isinstance(channel, di.GroupChannel):
            return

        if isinstance(channel, di.Thread):
            parent = channel.parent
            if isinstance(parent, di.TextChannel):
                channel = parent
            else:
                return

        # types: TextChannel, VoiceChannel, Thread, DMChannel, PartialMessageable, GroupChannel

        author = message.author

        if isinstance(channel, di.DMChannel):
            if author == self.user:
                return await self.on_carbon(message)

            contact = await self.get_contact(author)
            return await contact.send_message(message)

        if isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(channel.id)
            if author == self.user:
                async with self.session.send_lock:
                    fut = self.session.send_futures.pop(message.id, None)
                if fut is None:
                    participant = await muc.get_user_participant()
                else:
                    fut.set_result(True)
                    return
            else:
                participant = await muc.get_participant_by_discord_user(author)

            return await participant.send_message(message)

    async def on_carbon(self, message: di.Message):
        assert isinstance(message.channel, (di.DMChannel, di.TextChannel, di.Thread))

        async with self.session.send_lock:
            fut = self.session.send_futures.pop(message.id, None)

        if fut is None:
            if isinstance(message.channel, di.DMChannel):
                contact = await self.get_contact(message.channel.recipient)
                await contact.send_message(message, carbon=True)
            elif isinstance(message.channel, di.TextChannel):
                muc = await self.session.bookmarks.by_legacy_id(message.channel.id)
                participant = await muc.get_user_participant()
                participant.send_message(message)
            elif isinstance(message.channel, di.Thread):
                muc = await self.session.bookmarks.by_legacy_id(
                    message.channel.parent_id
                )
                participant = await muc.get_user_participant()
                participant.send_message(message)
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

        if before.content == after.content:
            # edit events are emitted on various occasion,
            # for instance when a thread is created
            return

        if isinstance(channel, di.DMChannel):
            correcter = await self.get_contact(channel.recipient)
            if after.author == self.user:
                return await self.on_carbon_edit(before, after, correcter)

        elif isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(after.channel.id)
            correcter = await muc.get_participant_by_discord_user(after.author)

        else:
            self.log.debug("Ignoring edit in: %s", after.channel)
            return

        correcter.correct(after.id, after.content)

    async def on_carbon_edit(
        self, before: di.Message, after: di.Message, contact: "Contact"
    ):
        fut = self.session.edit_futures.pop(after.id, None)
        if fut is None:
            return contact.correct(before.id, after.content, carbon=True)
        fut.set_result(True)

    async def on_message_delete(self, m: di.Message):
        own = m.author == self.user
        if own:
            fut = self.session.delete_futures.pop(m.id, None)
            if fut is not None:
                fut.set_result(True)
                return

        channel = m.channel
        if isinstance(channel, di.DMChannel):
            deleter = await self.get_contact(channel.recipient)
            if own:
                deleter.retract(m.id, carbon=True)
                return
        elif isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(m.channel.id)
            if own:
                deleter = await muc.get_user_participant()
            else:
                contact = await self.get_contact(m.author)
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
