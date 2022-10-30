import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Union

import aiotdlib.api as tgapi

from slidge import *

if TYPE_CHECKING:
    from .session import Session


async def noop():
    return


class Contact(LegacyContact["Session"]):
    legacy_id: int
    # Telegram official clients have no XMPP presence equivalent, but a 'last seen' indication.
    CLIENT_TYPE = "phone"

    def __init__(self, *a, **k):
        super(Contact, self).__init__(*a, **k)
        self._online_expire_task = self.xmpp.loop.create_task(noop())

    @staticmethod
    def _format_last_seen(timestamp: Union[int, float]):
        return f"Last seen {datetime.fromtimestamp(timestamp):%A %H:%M GMT}"

    async def _expire_online(self, timestamp: Union[int, float]):
        now = time.time()
        how_long = timestamp - now
        log.debug("Online status expires in %s seconds", how_long)
        await asyncio.sleep(how_long)
        self.away(self._format_last_seen(now))

    def update_status(self, status: tgapi.UserStatus):
        if isinstance(status, tgapi.UserStatusEmpty):
            self.inactive()
            self.offline()
        elif isinstance(status, tgapi.UserStatusLastMonth):
            self.inactive()
            self.extended_away("Offline since last month")
        elif isinstance(status, tgapi.UserStatusLastWeek):
            self.inactive()
            self.extended_away("Offline since last week")
        elif isinstance(status, tgapi.UserStatusOffline):
            self.inactive()
            if self._online_expire_task.done():
                # we've never seen the contact online, so we use the was_online timestamp
                self.away(self._format_last_seen(status.was_online))
        elif isinstance(status, tgapi.UserStatusOnline):
            self.online()
            self.active()
            self._online_expire_task.cancel()
            self._online_expire_task = self.xmpp.loop.create_task(
                self._expire_online(status.expires)
            )
        elif isinstance(status, tgapi.UserStatusRecently):
            self.inactive()
            self.away("Last seen recently")

    async def send_tg_message(self, msg: tgapi.Message):
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
        query = tgapi.DownloadFile.construct(
            file_id=best_file.id, synchronous=True, priority=1
        )
        best_file_downloaded: tgapi.File = await self.session.tg.request(query)
        await self.send_file(best_file_downloaded.local.path)
        if caption.text:
            self.send_text(caption.text, legacy_msg_id=msg_id)

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

        else:
            if user.is_contact:
                self._subscribe_to = True
                self._subscribe_from = user.is_mutual_contact
            else:
                self._subscribe_to = self._subscribe_from = False

        self.update_status(user.status)

        if p := user.phone_number:
            phone = "+" + p
        else:
            phone = None
        self.set_vcard(
            given=user.first_name, surname=user.last_name, phone=phone, full_name=name
        )

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
