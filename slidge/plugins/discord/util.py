from typing import Any, Union

import discord as di

from .session import Session


class Mixin:
    legacy_id: int  # type: ignore
    name: str  # type: ignore
    avatar: str  # type: ignore
    session: Session  # type: ignore
    discord_user: Union[di.User, di.ClientUser]

    MARKS = False

    def react(self, mid: int, e: list[str]):
        raise NotImplementedError

    def send_text(self, *a, **k):
        raise NotImplementedError

    def send_file(self, *a, **k):
        raise NotImplementedError

    async def update_reactions(self, m: di.Message):
        legacy_reactions = []
        user = self.discord_user
        for r in m.reactions:
            if r.is_custom_emoji():
                continue
            assert isinstance(r.emoji, str)
            async for u in r.users():
                # self.session.log.debug("uid userid %s %s", u, user)
                if u.id == user.id:
                    legacy_reactions.append(r.emoji)
        self.react(m.id, legacy_reactions)

    async def send_message(self, message: di.Message, **kwargs):
        reply_to = message.reference.message_id if message.reference else None
        reply_kwargs = dict[str, Any](reply_to=reply_to)
        if reply_to:
            reply_to_message = await message.channel.fetch_message(reply_to)
            reply_kwargs["reply_to_fallback_text"] = reply_to_message.content
            reply_kwargs["reply_self"] = reply_to_message.author == message.author

            if isinstance(message.channel, di.TextChannel):
                muc = await self.session.bookmarks.by_legacy_id(message.channel.id)
                if reply_to_message.author == self.session.discord.user:
                    reply_to_author = await muc.get_user_participant()
                else:
                    contact = await self.session.contacts.by_discord_user(
                        reply_to_message.author
                    )
                    reply_to_author = await muc.get_participant_by_contact(contact)
                reply_kwargs["reply_to_author"] = reply_to_author

        self.session.log.debug("REPLY TO KWARGS %s", reply_kwargs)

        text = message.content
        attachments = message.attachments
        msg_id = message.id

        if not attachments:
            return self.send_text(
                text,
                legacy_msg_id=msg_id,
                reply_to_msg_id=reply_to,
                **reply_kwargs,
                **kwargs,
            )

        last_attachment_i = len(attachments := message.attachments) - 1
        for i, attachment in enumerate(attachments):
            last = i == last_attachment_i
            await self.send_file(
                file_url=attachment.url,
                file_name=attachment.filename,
                content_type=attachment.content_type,
                legacy_msg_id=msg_id if last else None,
                caption=text if last else None,
                **reply_kwargs if last else {},
                **kwargs,
            )
