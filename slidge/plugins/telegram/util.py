import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import aiotdlib.api as tgapi

from slidge.util.error import XMPPError

from . import config

if TYPE_CHECKING:
    from .group import MUC
    from .session import Session


def get_best_file(content: tgapi.MessageContent) -> Optional[tgapi.File]:
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
    return None


def get_file_name(content: tgapi.MessageContent) -> Optional[str]:
    if isinstance(content, tgapi.MessageVideo):
        return content.video.file_name
    elif isinstance(content, tgapi.MessageAnimation):
        return content.animation.file_name
    elif isinstance(content, tgapi.MessageAudio):
        return content.audio.file_name
    elif isinstance(content, tgapi.MessageDocument):
        return content.document.file_name
    return None


class AvailableEmojisMixin:
    session: "Session"
    chat_id: int
    log: logging.Logger
    REACTIONS_SINGLE_EMOJI = True

    async def available_emojis(self, legacy_msg_id=None):
        if legacy_msg_id is None:
            try:
                chat = await self.session.tg.get_chat(self.chat_id)
            except XMPPError as e:
                self.log.debug(f"Could not get the available emojis: %s", e)
                return
            emojis = set(chat.available_reactions)
            return emojis

        available = await self.session.tg.api.get_message_available_reactions(
            chat_id=self.chat_id, message_id=legacy_msg_id
        )
        # TODO: figure out how we can actually determine if the user can use
        #       premium emojis
        # features = await self.session.tg.api.get_premium_features(
        #     None, skip_validation=True
        # )
        # self.session.log.debug("Premium features: %s", features)
        # for f in features.features:
        #     if isinstance(f, tgapi.PremiumFeatureUniqueReactions):
        #         return {a.reaction for a in available.reactions}
        return {a.reaction for a in available.reactions if not a.needs_premium}


class TelegramToXMPPMixin:
    session: "Session"  # type:ignore
    chat_id: int
    is_group: bool = NotImplemented
    muc: "MUC"

    def send_text(self, *a, **k):
        raise NotImplemented

    def send_file(self, *a, **k):
        raise NotImplemented

    async def _update_reply_to_kwargs(self, msg: tgapi.Message, kwargs):
        if not (reply_to := msg.reply_to_message_id):
            # if reply_to = 0, telegram really means "None"
            return

        # all of this is to provide the fallback part.
        # it's quite ugly, but seems to work...
        try:
            reply_to_msg = await self.session.tg.api.get_message(self.chat_id, reply_to)
        except XMPPError:
            kwargs["reply_to_msg_id"] = reply_to
            kwargs["reply_to_fallback"] = "[deleted message]"
            kwargs["reply_to_author"] = None
            kwargs["reply_self"] = False
            return

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
            muc = self.muc
            if sender_user_id is None:
                reply_to_fallback = ""
                reply_to_author = muc.get_system_participant()
            elif sender_user_id == await self.session.tg.get_my_id():
                reply_to_author = await muc.get_user_participant()
                reply_to_fallback = f"{muc.user_nick}:\n"
            else:
                reply_to_author = await muc.participant_by_tg_user_id(sender_user_id)
                reply_to_fallback = f"{reply_to_author.contact.name}:\n"
        else:
            reply_to_fallback = ""
            reply_to_author = None

        if isinstance(reply_to_content, tgapi.MessageText):
            reply_to_fallback += reply_to_content.text.text
        elif isinstance(reply_to_content, tgapi.MessageAnimatedEmoji):
            reply_to_fallback += reply_to_content.animated_emoji.sticker.emoji
        elif isinstance(reply_to_content, tgapi.MessageSticker):
            reply_to_fallback += reply_to_content.sticker.emoji
        elif best_file := get_best_file(reply_to_content):
            reply_to_fallback += f"Attachment {best_file.id}"
        else:
            reply_to_fallback += "[unsupported by slidge]"

        kwargs.update(
            dict(
                reply_to_msg_id=reply_to,
                reply_to_fallback_text=reply_to_fallback,
                reply_to_author=reply_to_author,
                reply_self=reply_self,
            )
        )

    async def send_tg_message(self, msg: tgapi.Message, **kwargs):
        content = msg.content
        kwargs.update(
            dict(
                legacy_msg_id=msg.id,
                when=datetime.fromtimestamp(msg.date),
            )
        )
        await self._update_reply_to_kwargs(msg, kwargs)

        self.session.log.debug("kwargs %s", kwargs)
        if isinstance(content, tgapi.MessageText):
            # TODO: parse formatted text to markdown
            formatted_text = content.text
            self.send_text(body=formatted_text.text, **kwargs)
        elif isinstance(content, tgapi.MessageAnimatedEmoji):
            emoji = content.animated_emoji.sticker.emoji
            self.send_text(body=emoji, **kwargs)
        elif isinstance(content, tgapi.MessageSticker):
            sticker = content.sticker
            sticker_type = sticker.type_
            if isinstance(sticker_type, tgapi.StickerTypeAnimated):
                if t := sticker.thumbnail:
                    await self.send_tg_file(t.file, **kwargs)
                else:
                    self.send_text(body="Sticker: " + sticker.emoji, **kwargs)
            else:
                await self.send_tg_file(sticker.sticker, **kwargs)
        elif best_file := get_best_file(content):
            await self.send_tg_file(
                best_file, content.caption.text, get_file_name(content), **kwargs
            )
        elif isinstance(content, tgapi.MessageBasicGroupChatCreate):
            # TODO: work out how to map this to group invitation
            pass
        elif isinstance(content, tgapi.MessageChatAddMembers):
            muc = self.muc
            for user_id in content.member_user_ids:
                participant = await muc.participant_by_tg_user_id(user_id)
                participant.online()
        elif isinstance(content, tgapi.MessagePinMessage):
            if await self.session.tg.is_private_chat(msg.chat_id):
                return
            muc = self.muc
            await muc.update_subject_from_msg()
        elif isinstance(content, tgapi.MessageCustomServiceAction):
            self.send_text(body=content.text, **kwargs)
        else:
            self.send_text(
                "/me tried to send an unsupported content. "
                "Please report this: https://todo.sr.ht/~nicoco/slidge",
                **kwargs,
            )
            self.session.log.warning("Ignoring content: %s", type(content))

    async def send_tg_file(
        self,
        best_file: tgapi.File,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        **kwargs,
    ):
        query = tgapi.DownloadFile.construct(
            file_id=best_file.id, synchronous=True, priority=1
        )
        size = best_file.size
        if size > config.ATTACHMENT_MAX_SIZE:
            return self.send_text(
                f"/me tried to send an attachment larger than {config.ATTACHMENT_MAX_SIZE}",
                **kwargs,
            )
        try:
            best_file_downloaded: tgapi.File = await self.session.tg.request(query)
        except XMPPError as e:
            return self.send_text(
                f"/me tried to send an attachment but something went wrong: {e.text}",
                **kwargs,
            )
        await self.send_file(
            best_file_downloaded.local.path,
            caption=caption,
            file_name=file_name,
            legacy_file_id=str(best_file.remote.unique_id),
            **kwargs,
        )
