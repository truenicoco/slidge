from typing import TYPE_CHECKING, Union

import discord as di

if TYPE_CHECKING:
    from .session import Session


class Discord(di.Client):
    def __init__(self, session: "Session"):
        super().__init__()
        self.session = session

    async def on_ready(self):
        self.session.ready_future.set_result(True)
        print(f"Logged on as {self.user}")

    async def on_message(self, message: di.Message):
        if (author := message.author) == self.user:
            async with self.session.send_lock:
                fut = self.session.send_futures.get(message.id)
            if fut is None:
                channel = message.channel
                if isinstance(channel, di.DMChannel):
                    self.session.contacts.by_discord_user(channel.recipient).carbon(
                        message.content
                    )
                else:
                    self.session.log.debug("Ignoring group chat carbon")
            else:
                fut.set_result(True)
        else:
            contact = self.session.contacts.by_discord_user(author)
            reply_to = message.reference.message_id if message.reference else None
            if content := message.content:
                contact.send_text(
                    content,
                    legacy_msg_id=message.id,
                    reply_to_msg_id=reply_to,
                )
            for attachment in message.attachments:
                await contact.send_file(
                    url=attachment.url,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    reply_to_msg_id=reply_to,
                )

    async def on_typing(self, channel, user, _when):
        if user != self.user and isinstance(channel, di.DMChannel):
            self.session.contacts.by_discord_user(user).composing()

    async def on_message_edit(self, before: di.Message, after: di.Message):
        if not isinstance(after.channel, di.DMChannel):
            return
        if before.content == after.content:
            return
        if (author := after.author) == self.user:
            fut = self.session.edit_futures.get(after.id)
            if fut is None:
                self.session.contacts.by_discord_user(
                    after.channel.recipient
                ).carbon_correct(after.id, after.content)
            else:
                fut.set_result(True)
        else:
            self.session.contacts.by_discord_user(author).correct(
                after.id, after.content
            )

    async def on_message_delete(self, m: di.Message):
        if not isinstance(m.channel, di.DMChannel):
            return
        if (author := m.author) == self.user:
            fut = self.session.delete_futures.get(m.id)
            if fut is None:
                self.session.contacts.by_discord_user(
                    m.channel.recipient
                ).carbon_retract(m.id)
            else:
                fut.set_result(True)
        else:
            self.session.contacts.by_discord_user(author).retract(m.id)

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

        if user == self.user:
            self.session.update_reactions(message)
        else:
            await self.session.contacts.by_discord_user(user).update_reactions(message)
