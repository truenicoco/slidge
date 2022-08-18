import asyncio
import datetime
import logging
from typing import TYPE_CHECKING

import aiotdlib.api as tgapi

from slidge import *

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session"]):
    legacy_id: int
    # Telegram official clients have no XMPP presence equivalent, but a 'last seen' indication.
    AWAY_DELAY = 300

    def __init__(self, *a, **k):
        super(Contact, self).__init__(*a, **k)
        self.last_seen = datetime.datetime.fromtimestamp(0)
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
        super().away(status or f"Last seen: {self.last_seen:%%B %d, %Y}")

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


class Roster(LegacyRoster["Contact", "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


log = logging.getLogger(__name__)
