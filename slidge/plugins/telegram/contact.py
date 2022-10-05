import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Optional

import aiotdlib.api as tgapi

from slidge import *

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session"]):
    legacy_id: int
    # Telegram official clients have no XMPP presence equivalent, but a 'last seen' indication.
    AWAY_DELAY = 300
    CLIENT_TYPE = "phone"

    def __init__(self, *a, **k):
        super(Contact, self).__init__(*a, **k)
        self.last_seen = None
        self.away_task: asyncio.Task = self.xmpp.loop.create_task(self.delayed_away())

    def reset_delayed_away(self):
        self.away_task.cancel()
        self.last_seen = datetime.datetime.now()
        self.online()
        self.away_task = self.xmpp.loop.create_task(self.delayed_away())

    async def delayed_away(self):
        await asyncio.sleep(60)
        for x in range(1, 60):
            self.away(f"Last seen {x} minute{'s' if x > 1 else ''} ago")
            await asyncio.sleep(60)
        for x in range(1, 24):
            self.away(f"Last seen {x} hour{'s' if x > 1 else ''} ago")
            await asyncio.sleep(3600)
        self.away(f"Last seen yesterday")
        await asyncio.sleep(3600 * 24)
        for x in range(2, 7):
            self.away(f"Last seen {x} day{'s' if x > 1 else ''} ago")
            await asyncio.sleep(3600 * 24)
        self.away()

    def away(self, status=None):
        if status is None:
            if self.last_seen is None:
                status = "Last seen: never"
            else:
                status = f"{self.last_seen: %B %d, %Y}"
        super().away(status)

    def active(self):
        self.reset_delayed_away()
        self.online()
        super().active()

    async def send_tg_message(self, msg: tgapi.Message):
        self.reset_delayed_away()
        content = msg.content
        if isinstance(content, tgapi.MessageText):
            # TODO: parse formatted text to markdown
            formatted_text = content.text
            self.send_text(
                body=formatted_text.text,
                legacy_msg_id=msg.id,
                reply_to_msg_id=msg.reply_to_message_id,
            )
        elif isinstance(content, tgapi.MessageAnimatedEmoji):
            emoji = content.animated_emoji.sticker.emoji
            self.send_text(
                body=emoji,
                legacy_msg_id=msg.id,
                reply_to_msg_id=msg.reply_to_message_id,
            )
        elif isinstance(content, tgapi.MessagePhoto):
            photo = content.photo
            best_file = max(photo.sizes, key=lambda x: x.width).photo
            await self.send_tg_file(best_file, content.caption, msg.id)
        elif isinstance(content, tgapi.MessageVideo):
            best_file = content.video.video
            await self.send_tg_file(best_file, content.caption, msg.id)
        elif isinstance(content, tgapi.MessageAnimation):
            best_file = content.animation.animation
            await self.send_tg_file(best_file, content.caption, msg.id)
        else:
            self.session.log.debug("Ignoring content: %s", type(content))

    async def send_tg_file(self, best_file, caption, msg_id):
        self.reset_delayed_away()
        query = tgapi.DownloadFile.construct(
            file_id=best_file.id, synchronous=True, priority=1
        )
        best_file_downloaded: tgapi.File = await self.session.tg.request(query)
        await self.send_file(best_file_downloaded.local.path)
        if caption.text:
            self.send_text(caption.text, legacy_msg_id=msg_id)

    async def send_tg_status(self, status: tgapi.UserStatus):
        self.reset_delayed_away()
        if isinstance(status, tgapi.UserStatusOnline):
            self.active()
        elif isinstance(status, tgapi.UserStatusOffline):
            self.paused()
            self.inactive()
        else:
            log.debug("Ignoring status %s", status)

    async def update_info_from_user(self, user: Optional[tgapi.User] = None):
        if user is None:
            user = await self.session.tg.api.get_user(self.legacy_id)
        if username := user.username:
            name = username
        else:
            name = user.first_name
            if last := user.last_name:
                name += " " + last
        self.name = name
        # TODO: use user.status
        if photo := user.profile_photo:
            if (local := photo.small.local) and (path := local.path):
                with open(path, "rb") as f:
                    self.avatar = f.read()
            else:
                response = await self.session.tg.api.download_file(
                    file_id=photo.small.id,
                    synchronous=True,
                    priority=1,
                    offset=0,
                    limit=0,
                )
                with open(response.local.path, "rb") as f:
                    self.avatar = f.read()
        if isinstance(user.type_, tgapi.UserTypeBot) or user.id == 777000:
            # 777000 is not marked as bot, it's the "Telegram" contact, which gives
            # confirmation codes and announces telegram-related stuff
            self.CLIENT_TYPE = "bot"

    async def update_info_from_chat(self, chat: tgapi.Chat):
        self.name = chat.title
        if isinstance(chat.photo, tgapi.ChatPhotoInfo):
            if (local := chat.photo.small.local) and (path := local.path):
                with open(path, "rb") as f:
                    self.avatar = f.read()
            else:
                response = await self.session.tg.api.download_file(
                    file_id=chat.photo.small.id,
                    synchronous=True,
                    priority=1,
                    offset=0,
                    limit=0,
                )
                with open(response.local.path, "rb") as f:
                    self.avatar = f.read()


class Roster(LegacyRoster["Contact", "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


log = logging.getLogger(__name__)
