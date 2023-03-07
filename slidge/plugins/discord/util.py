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

    async def get_reply_to_kwargs(self, message: di.Message):
        quoted_msg_id = message.reference.message_id if message.reference else None

        reply_kwargs = dict[str, Any]()
        if not quoted_msg_id:
            return None, reply_kwargs

        reply_kwargs["reply_to_msg_id"] = quoted_msg_id

        try:
            if message.type == di.MessageType.thread_starter_message:
                assert isinstance(message.channel, di.Thread)
                assert isinstance(message.channel.parent, di.TextChannel)
                quoted_msg = await message.channel.parent.fetch_message(quoted_msg_id)
            else:
                quoted_msg = await message.channel.fetch_message(quoted_msg_id)
        except di.errors.NotFound:
            reply_kwargs = {
                "reply_to_fallback_text": "[quoted message could not be fetched]"
            }
            quoted_msg = None
        else:
            assert quoted_msg is not None
            reply_kwargs["reply_to_fallback_text"] = quoted_msg.content
            reply_kwargs["reply_self"] = quoted_msg.author == message.author

        return quoted_msg, reply_kwargs

    async def send_message(self, message: di.Message, archive_only=False):
        _, reply_kwargs = await self.get_reply_to_kwargs(message)

        self.session.log.debug("REPLY TO KWARGS %s", reply_kwargs)

        mtype = message.type
        if mtype == di.MessageType.thread_created:
            text = f"/me created a thread named '{message.content}'"
        elif mtype == di.MessageType.thread_starter_message:
            text = f"I started a new thread from this message ↑"
        else:
            text = message.content

        attachments = message.attachments
        msg_id = message.id

        channel = message.channel
        if isinstance(channel, di.Thread):
            thread = channel.id
            if message.type == di.MessageType.channel_name_change:
                text = f"/me renamed this thread: {text}"
        else:
            thread = None

        if not attachments:
            return self.send_text(
                text,
                legacy_msg_id=msg_id,
                when=message.created_at,
                thread=thread,
                **reply_kwargs,
                archive_only=archive_only,
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
                thread=thread,
                **reply_kwargs if last else {},
                archive_only=archive_only,
                when=message.created_at,
            )
