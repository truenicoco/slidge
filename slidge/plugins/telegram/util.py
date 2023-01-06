from datetime import datetime
from typing import TYPE_CHECKING

import aiotdlib.api as tgapi

if TYPE_CHECKING:
    from .session import Session


def get_best_file(content: tgapi.MessageContent):
    if isinstance(content, tgapi.MessagePhoto):
        photo = content.photo
        return max(photo.sizes, key=lambda x: x.width).photo
    elif isinstance(content, tgapi.MessageVideo):
        return content.video.video
    elif isinstance(content, tgapi.MessageAnimation):
        return content.animation.animation
    elif isinstance(content, tgapi.MessageAudio):
        return content.audio.audio
    elif isinstance(content, tgapi.MessageDocument):
        return content.document.document


class AvailableEmojisMixin:
    session: "Session"
    chat_id: int

    async def available_emojis(self, legacy_msg_id):
        available = await self.session.tg.api.get_message_available_reactions(
            chat_id=self.chat_id, message_id=legacy_msg_id
        )
        return {a.reaction for a in available.reactions}


class TelegramToXMPPMixin:
    session: "Session"  # type:ignore
    chat_id: int
    is_group: bool = NotImplemented

    def send_text(self, *a, **k):
        raise NotImplemented

    def send_file(self, *a, **k):
        raise NotImplemented

    async def send_tg_message(self, msg: tgapi.Message, **kwargs):
        content = msg.content
        reply_to = msg.reply_to_message_id
        if reply_to:
            try:
                reply_to_msg = await self.session.tg.api.get_message(
                    self.chat_id, reply_to
                )
            except tgapi.NotFound:
                # apparently in telegram it is possible to "reply-to" messages that have been deleted
                # TODO: mention in the body that this is reply to a deleted message
                reply_to = None
                reply_to_fallback = None
                reply_to_author = None
                reply_self = False
            else:
                reply_to_content = reply_to_msg.content
                reply_to_sender = reply_to_msg.sender_id
                if isinstance(reply_to_sender, tgapi.MessageSenderUser):
                    sender_user_id = reply_to_sender.user_id
                    reply_self = (
                        isinstance(msg.sender_id, tgapi.MessageSenderUser)
                        and sender_user_id == msg.sender_id.user_id
                    )
                elif isinstance(reply_to_sender, tgapi.MessageSenderChat):
                    reply_self = isinstance(msg.sender_id, tgapi.MessageSenderChat)
                    sender_user_id = None
                else:
                    raise RuntimeError("This should not happen")

                if self.is_group and not reply_self:
                    muc = await self.session.bookmarks.by_legacy_id(msg.chat_id)
                    if sender_user_id is None:
                        reply_to_author = await muc.participant_system()
                    else:
                        reply_to_author = await muc.participant_by_tg_user_id(
                            sender_user_id
                        )
                else:
                    reply_to_author = None

                if isinstance(reply_to_content, tgapi.MessageText):
                    reply_to_fallback = reply_to_content.text.text
                elif isinstance(reply_to_content, tgapi.MessageAnimatedEmoji):
                    reply_to_fallback = reply_to_content.animated_emoji.sticker.emoji
                elif isinstance(reply_to_content, tgapi.MessageSticker):
                    reply_to_fallback = reply_to_content.sticker.emoji
                elif best_file := get_best_file(reply_to_content):
                    reply_to_fallback = f"Attachment {best_file.id}"
                else:
                    reply_to_fallback = "[unsupported by slidge]"
        else:
            # if reply_to = 0, telegram really means "None"
            reply_to = None
            reply_to_fallback = None
            reply_to_author = None
            reply_self = False

        kwargs.update(
            dict(
                legacy_msg_id=msg.id,
                reply_to_msg_id=reply_to,
                reply_to_fallback_text=reply_to_fallback,
                reply_to_author=reply_to_author,
                reply_self=reply_self,
                when=datetime.fromtimestamp(msg.date),
            )
        )
        self.session.log.debug("kwargs %s", kwargs)
        if isinstance(content, tgapi.MessageText):
            # TODO: parse formatted text to markdown
            formatted_text = content.text
            self.send_text(body=formatted_text.text, **kwargs)
        elif isinstance(content, tgapi.MessageAnimatedEmoji):
            emoji = content.animated_emoji.sticker.emoji
            self.send_text(body=emoji, **kwargs)
        elif isinstance(content, tgapi.MessageSticker):
            emoji = content.sticker.emoji
            self.send_text(body="[Sticker] " + emoji, **kwargs)
        elif best_file := get_best_file(content):
            await self.send_tg_file(best_file, content.caption.text, **kwargs)
        elif isinstance(content, tgapi.MessageBasicGroupChatCreate):
            # TODO: work out how to map this to group invitation
            pass
        elif isinstance(content, tgapi.MessageChatAddMembers):
            muc = await self.session.bookmarks.by_legacy_id(msg.chat_id)
            for user_id in content.member_user_ids:
                participant = await muc.participant_by_tg_user_id(user_id)
                participant.online()
        elif isinstance(content, tgapi.MessagePinMessage):
            if await self.session.tg.is_private_chat(msg.chat_id):
                return
            muc = await self.session.bookmarks.by_legacy_id(msg.chat_id)
            await muc.update_subject_from_msg()
        else:
            self.send_text(
                "/me tried to send an unsupported content. "
                "Please report this: https://todo.sr.ht/~nicoco/slidge",
                **kwargs,
            )
            self.session.log.warning("Ignoring content: %s", type(content))

    async def send_tg_file(self, best_file: tgapi.File, caption: str, **kwargs):
        query = tgapi.DownloadFile.construct(
            file_id=best_file.id, synchronous=True, priority=1
        )
        best_file_downloaded: tgapi.File = await self.session.tg.request(query)
        await self.send_file(best_file_downloaded.local.path, caption=caption, **kwargs)
