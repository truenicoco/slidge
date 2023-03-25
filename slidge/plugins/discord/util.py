from typing import TYPE_CHECKING, Union

import discord as di

from slidge.core.mixins.message import ContentMessageMixin
from slidge.util.types import LegacyAttachment, MessageReference

if TYPE_CHECKING:
    from .group import MUC


class Mixin(ContentMessageMixin):
    legacy_id: int  # type:ignore
    avatar: str
    discord_user: Union[di.User, di.ClientUser]

    MARKS = False

    async def update_reactions(self, m: di.Message):
        legacy_reactions = []
        user = self.discord_user
        for r in m.reactions:
            if r.is_custom_emoji():
                continue
            assert isinstance(r.emoji, str)
            try:
                async for u in r.users():
                    if u.id == user.id:
                        legacy_reactions.append(r.emoji)
            except di.NotFound:
                # the message has now been deleted
                # seems to happen quite a lot. I guess
                # there are moderation bot that are triggered
                # by reactions from users
                # oh, discord…
                return
        self.react(m.id, legacy_reactions)

    async def _reply_to(self, message: di.Message):
        if not (ref := message.reference):
            return

        quoted_msg_id = ref.message_id
        if quoted_msg_id is None:
            return

        reply_to = MessageReference(quoted_msg_id)

        try:
            if message.type == di.MessageType.thread_starter_message:
                assert isinstance(message.channel, di.Thread)
                assert isinstance(message.channel.parent, di.TextChannel)
                quoted_msg = await message.channel.parent.fetch_message(quoted_msg_id)
            else:
                quoted_msg = await message.channel.fetch_message(quoted_msg_id)
        except di.errors.NotFound:
            reply_to.body = "[quoted message could not be fetched]"
            return reply_to

        reply_to.body = quoted_msg.content
        author = quoted_msg.author
        if author == self.discord_user:
            reply_to.author = self.session.user
            return reply_to

        muc: "MUC" = getattr(self, "muc", None)  # type: ignore
        if muc:
            reply_to.author = await muc.get_participant_by_discord_user(author)
        else:
            reply_to.author = self  # type: ignore

        return reply_to

    async def send_message(self, message: di.Message, archive_only=False):
        reply_to = await self._reply_to(message)

        mtype = message.type
        if mtype == di.MessageType.thread_created:
            text = f"/me created a thread named '{message.content}'"
        elif mtype == di.MessageType.thread_starter_message:
            text = f"I started a new thread from this message ↑"
        else:
            text = message.content

        channel = message.channel
        if isinstance(channel, di.Thread):
            thread = channel.id
            if message.type == di.MessageType.channel_name_change:
                text = f"/me renamed this thread: {text}"
        else:
            thread = None

        await self.send_files(
            attachments=[Attachment.from_discord(a) for a in message.attachments],
            legacy_msg_id=message.id,
            when=message.created_at,
            thread=thread,
            body=text,
            reply_to=reply_to,
            archive_only=archive_only,
        )


class Attachment(LegacyAttachment):
    @staticmethod
    def from_discord(di_attachment: di.Attachment):
        return Attachment(
            url=di_attachment.url,
            name=di_attachment.filename,
            content_type=di_attachment.content_type,
            legacy_file_id=di_attachment.id,
        )
