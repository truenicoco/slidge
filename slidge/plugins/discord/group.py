import asyncio
from typing import Any, Union

import discord as di
import discord.errors

from slidge import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType, XMPPError

from . import config
from .contact import Contact
from .session import Session
from .util import Mixin


class Bookmarks(LegacyBookmarks):
    async def fill(self):
        for channel in self.session.discord.get_all_channels():
            if isinstance(channel, di.TextChannel):
                await self.by_legacy_id(channel.id)


class Participant(LegacyParticipant, Mixin):  # type: ignore
    session: Session
    contact: "Contact"

    async def get_reply_to_kwargs(self, message: di.Message):
        quoted_msg, reply_kwargs = await super().get_reply_to_kwargs(message)
        if not quoted_msg:
            return None, reply_kwargs

        muc = self.muc
        if quoted_msg.author == self.session.discord.user:
            reply_to_author = await muc.get_user_participant()
        else:
            contact = await self.session.contacts.by_discord_user(quoted_msg.author)
            reply_to_author = await muc.get_participant_by_contact(contact)
        reply_kwargs["reply_to_author"] = reply_to_author

        return quoted_msg, reply_kwargs

    @property
    def discord_user(self) -> Union[di.User, di.ClientUser]:  # type:ignore
        if self.contact is None:
            return self.session.discord.user  # type:ignore

        return self.contact.discord_user


class MUC(LegacyMUC[Session, int, Participant, int]):
    session: Session
    type = MucType.GROUP

    async def get_discord_channel(self) -> di.TextChannel:
        await self.session.ready_future
        return self.session.discord.get_channel(self.legacy_id)  # type: ignore

    async def get_user_participant(self):
        p = await super().get_user_participant()
        p.discord_id = self.session.discord.user.id  # type:ignore
        return p

    async def get_participants(self, max_=50):
        chan = await self.get_discord_channel()
        for m in chan.members[:max_]:
            if m.id == self.session.discord.user.id:  # type:ignore
                continue
            co = await self.session.contacts.by_discord_user(m)
            yield await self.get_participant_by_contact(co)

    async def update_info(self):
        while not (chan := await self.get_discord_channel()):
            await asyncio.sleep(0.1)

        while not chan.guild.name and not chan.name:
            await asyncio.sleep(0.1)

        if chan.category:
            self.name = (
                f"{chan.guild.name}/{chan.position:02d}/{chan.category}/{chan.name}"
            )
        else:
            self.name = f"{chan.guild.name}/{chan.position:02d}/{chan.name}"
        self.subject = chan.topic

        self.n_participants = chan.guild.approximate_member_count

    async def backfill(self):
        try:
            await self.history()
        except discord.errors.Forbidden:
            self.log.warning("Could not fetch history of %r", self.name)

    async def history(self):
        if not config.MUC_BACK_FILL:
            return

        chan = await self.get_discord_channel()

        messages = [msg async for msg in chan.history(limit=config.MUC_BACK_FILL)]
        self.log.debug("Fetched %s messages for %r", len(messages), self.name)
        for i, msg in enumerate(reversed(messages)):
            self.log.debug("Message %s", i)
            author = msg.author
            if author.id == self.session.discord.user.id:  # type:ignore
                p = await self.get_user_participant()
            else:
                try:
                    p = await self.get_participant_by_contact(
                        await self.session.contacts.by_discord_user(author)
                    )
                except XMPPError:
                    # deleted users
                    p = await self.get_participant(author.name)
            await p.send_message(msg, archive_only=True)
