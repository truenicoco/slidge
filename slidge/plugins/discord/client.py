from typing import TYPE_CHECKING, Any, Dict, Optional, Union

import discord as di
from aiohttp import BasicAuth

if TYPE_CHECKING:
    from .contact import Contact
    from .group import Participant
    from .session import Session


MessageableChannel = Union[
    di.TextChannel,
    di.VoiceChannel,
    di.Thread,
    di.DMChannel,
    di.PartialMessageable,
    di.GroupChannel,
    di.PartialMessageable,
    di.StageChannel,
]
Author = Union[di.User, di.Member, di.ClientUser]


class CaptchaHandler(di.CaptchaHandler):
    def __init__(self, session: "Session"):
        self.session = session

    async def fetch_token(
        self,
        data: Dict[str, Any],
        proxy: Optional[str],
        proxy_auth: Optional[BasicAuth],
        /,
    ) -> str:
        return await self.session.input(
            "You need to complete a captcha to be able to continue using "
            f"discord. Maybe you'll find some useful info here: {data}. If you "
            "do, you can reply here with the captcha token."
        )


class Discord(di.Client):
    def __init__(self, session: "Session"):
        self.session = session
        super().__init__(captcha_handler=CaptchaHandler(session))
        self.log = session.log
        self.ignore_next_msg_event = set[int]()

    def __ignore(self, mid: int):
        if mid in self.ignore_next_msg_event:
            self.ignore_next_msg_event.remove(mid)
            return True
        return False

    async def on_message(self, message: di.Message):
        async with self.session.send_lock:
            if self.__ignore(message.id):
                return

        if sender := await self.get_sender_by_message(message):
            await sender.send_message(message)

    async def on_typing(self, channel: MessageableChannel, user: Author, _when):
        if user == self.user:
            return

        if contact := await self.get_sender(author=user, channel=channel):
            contact.composing()

    async def on_message_edit(self, before: di.Message, after: di.Message):
        if before.content == after.content:
            # edit events are emitted on various occasion,
            # for instance when a thread is created
            return

        if self.__ignore(after.id):
            return

        if sender := await self.get_sender_by_message(after):
            await sender.send_message(after, correction=True)

    async def on_message_delete(self, m: di.Message):
        carbon = m.author == self.user
        if self.__ignore(m.id):
            return

        if deleter := await self.get_sender_by_message(m):
            deleter.retract(m.id, carbon=carbon)

    async def on_reaction_add(self, reaction: di.Reaction, user: Author):
        await self.update_reactions(reaction, user)

    async def on_reaction_remove(self, reaction: di.Reaction, user: Author):
        await self.update_reactions(reaction, user)

    async def update_reactions(self, reaction: di.Reaction, user: Author):
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

    async def on_presence_update(
        self,
        _before: Union[di.Member, di.Relationship],
        after: Union[di.Member, di.Relationship],
    ):
        if not self.user:
            # should not happen (receiving presences when not logged)
            return

        if after.id == self.user.id:
            # we don't care about self presences
            return

        if isinstance(after, di.Relationship):
            await self.on_friend_presence_update(after)
        elif isinstance(after, di.Member):
            await self.on_guild_presence_update(after)

    async def on_friend_presence_update(self, friend: di.Relationship):
        if not friend.type == di.RelationshipType.friend:
            return
        c = await self.session.contacts.by_discord_user(friend.user)
        c.update_status(friend.status, friend.activity)

    async def on_guild_presence_update(self, member: di.Member):
        guild = member.guild
        # contact = await self.session.contacts.by_discord_user(member.user)
        for channel in guild.channels:
            if not isinstance(channel, di.TextChannel):
                continue
            muc = await self.session.bookmarks.by_legacy_id(channel.id)
            participant = await muc.get_participant_by_legacy_id(member.id)
            participant.update_status(member.status, member.activity)

    async def get_contact(self, user: Union[di.User, di.Member]):
        return await self.session.contacts.by_discord_user(user)

    async def get_sender_by_message(self, message: di.Message):
        return await self.get_sender(message.author, message.channel)

    async def get_sender(
        self,
        author: Author,
        channel: MessageableChannel,
    ) -> Optional[Union["Contact", "Participant"]]:
        if isinstance(channel, di.Thread):
            parent = channel.parent
            if isinstance(parent, di.TextChannel):
                channel = parent
            else:
                self.log.debug("Ignoring thread of %s", parent)
                return None

        if isinstance(channel, di.DMChannel):
            if isinstance(author, di.ClientUser):
                return await self.get_contact(channel.recipient)
            else:
                return await self.get_contact(author)

        if isinstance(channel, di.TextChannel):
            muc = await self.session.bookmarks.by_legacy_id(channel.id)
            return await muc.get_participant_by_discord_user(author)

        self.log.debug("Could not get the sender %s of %s", author, channel)
        return None
