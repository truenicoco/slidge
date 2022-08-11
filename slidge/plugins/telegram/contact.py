import logging
from typing import TYPE_CHECKING

import aiotdlib.api as tgapi

from slidge import *

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session"]):
    legacy_id: int

    async def send_tg_message(self, msg: tgapi.Message):
        content = msg.content
        if isinstance(content, tgapi.MessageText):
            # TODO: parse formatted text to markdown
            formatted_text = content.text
            self.send_text(body=formatted_text.text, legacy_msg_id=msg.id)
        elif isinstance(content, tgapi.MessageAnimatedEmoji):
            emoji = content.animated_emoji.sticker.emoji
            self.send_text(body=emoji, legacy_msg_id=msg.id)
        elif isinstance(content, tgapi.MessagePhoto):
            photo = content.photo
            best_file = max(photo.sizes, key=lambda x: x.width).photo
            await self.send_tg_file(best_file, content.caption, msg.id)
        elif isinstance(content, tgapi.MessageVideo):
            best_file = content.video.video
            await self.send_tg_file(best_file, content.caption, msg.id)
        else:
            self.session.log.debug("Ignoring content: %s", type(content))

    async def send_tg_file(self, best_file, caption, msg_id):
        query = tgapi.DownloadFile.construct(
            file_id=best_file.id, synchronous=True, priority=1
        )
        best_file_downloaded: tgapi.File = await self.session.tg.request(query)
        await self.send_file(best_file_downloaded.local.path)
        if caption.text:
            self.send_text(caption.text, legacy_msg_id=msg_id)

    async def send_tg_status(self, status: tgapi.UserStatus):
        if isinstance(status, tgapi.UserStatusOnline):
            self.active()
        elif isinstance(status, tgapi.UserStatusOffline):
            self.paused()
            self.inactive()
        else:
            log.debug("Ignoring status %s", status)


class Roster(LegacyRoster["Contact", "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


log = logging.getLogger(__name__)
