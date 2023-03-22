import asyncio
from datetime import datetime
from typing import Optional, Union

import discord as di
import discord.errors

from slidge import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType, XMPPError

from . import config
from .contact import Contact
from .session import Session
from .util import Mixin


class Bookmarks(LegacyBookmarks[int, "MUC"]):
    session: Session

    async def fill(self):
        for channel in self.session.discord.get_all_channels():
            if isinstance(channel, di.TextChannel):
                await self.by_legacy_id(channel.id)


class Participant(LegacyParticipant, Mixin):  # type: ignore
    session: Session
    contact: Contact

    async def get_reply_to_kwargs(self, message: di.Message):
        quoted_msg, reply_kwargs = await super().get_reply_to_kwargs(message)
        if not quoted_msg:
            return None, reply_kwargs

        muc = self.muc
        if quoted_msg.author == self.session.discord.user:
            reply_to_author = await muc.get_user_participant()
        else:
            try:
                contact = await self.session.contacts.by_discord_user(quoted_msg.author)
            except XMPPError:
                reply_to_author = await muc.get_participant(str(quoted_msg.author))
            else:
                reply_to_author = await muc.get_participant_by_contact(contact)
        reply_kwargs["reply_to_author"] = reply_to_author

        return quoted_msg, reply_kwargs

    @property
    def discord_user(self) -> Union[di.User, di.ClientUser]:  # type:ignore
        if self.contact is None:
            return self.session.discord.user  # type:ignore

        return self.contact.discord_user


class MUC(LegacyMUC[int, int, Participant, int]):
    session: Session
    type = MucType.GROUP

    async def get_discord_channel(self) -> di.TextChannel:
        await self.session.discord.wait_until_ready()
        return self.session.discord.get_channel(self.legacy_id)  # type: ignore

    async def get_user_participant(self):
        p = await super().get_user_participant()
        p.discord_id = self.session.discord.user.id  # type:ignore
        return p

    async def fill_participants(self, max_=50):
        chan = await self.get_discord_channel()
        for m in chan.members[:max_]:
            if m.id == self.session.discord.user.id:  # type:ignore
                await self.get_user_participant()
                continue
            co = await self.session.contacts.by_discord_user(m)
            await self.get_participant_by_contact(co)

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
        if icon := chan.guild.icon:
            self.avatar = str(icon)

    async def backfill(self, oldest_id=None, oldest_date=None):
        try:
            await self.history(oldest_date)
        except discord.errors.Forbidden:
            self.log.warning("Could not fetch history of %r", self.name)

    async def history(self, oldest: Optional[datetime] = None):
        if not config.MUC_BACK_FILL:
            return

        chan = await self.get_discord_channel()

        messages = [
            msg async for msg in chan.history(limit=config.MUC_BACK_FILL, before=oldest)
        ]
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

    async def get_participant_by_discord_user(self, user: di.User):
        if user.discriminator == "0000":
            # a webhook, eg Github#0000
            # FIXME: avatars for contact-less participants
            p = await self.get_participant(user.display_name)
            p.DISCO_CATEGORY = "bot"
            return p
        try:
            return await self.get_participant_by_legacy_id(user.id)
        except XMPPError as e:
            self.log.warning(
                "Could not get participant with contact for %s, "
                "falling back to a 'contact-less' participant.",
                user,
                exc_info=e,
            )
            return await self.get_participant(user.display_name)

    async def create_thread(self, xmpp_id: str) -> int:
        ch = await self.get_discord_channel()

        try:
            thread_id = int(xmpp_id)
        except ValueError:
            pass
        else:
            if thread_id in (t.id for t in ch.threads):
                return thread_id

        thread = await ch.create_thread(name=xmpp_id, type=di.ChannelType.public_thread)
        return thread.id
