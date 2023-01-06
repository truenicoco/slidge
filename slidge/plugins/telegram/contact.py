import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Union

import aiotdlib.api as tgapi
from slixmpp.exceptions import XMPPError

from slidge import *

from .util import AvailableEmojisMixin, TelegramToXMPPMixin

if TYPE_CHECKING:
    from .session import Session


async def noop():
    return


class Contact(AvailableEmojisMixin, LegacyContact["Session", int], TelegramToXMPPMixin):
    CLIENT_TYPE = "phone"
    session: "Session"  # type:ignore

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.chat_id = self.legacy_id
        self._online_expire_task = self.xmpp.loop.create_task(noop())

    async def _expire_online(self, timestamp: Union[int, float]):
        now = time.time()
        how_long = timestamp - now
        log.debug("Online status expires in %s seconds", how_long)
        await asyncio.sleep(how_long)
        self.away(last_seen=datetime.fromtimestamp(timestamp))

    def update_status(self, status: tgapi.UserStatus):
        if isinstance(status, tgapi.UserStatusEmpty):
            self.inactive()
            self.offline()
        elif isinstance(status, tgapi.UserStatusLastMonth):
            self.inactive()
            self.extended_away(
                "Offline since last month"
                if global_config.LAST_SEEN_FALLBACK
                else None,
                last_seen=datetime.now() - timedelta(days=31),
            )
        elif isinstance(status, tgapi.UserStatusLastWeek):
            self.inactive()
            self.extended_away(
                "Offline since last week" if global_config.LAST_SEEN_FALLBACK else None,
                last_seen=datetime.now() - timedelta(days=7),
            )
        elif isinstance(status, tgapi.UserStatusOffline):
            self.inactive()
            if self._online_expire_task.done():
                # we've never seen the contact online, so we use the was_online timestamp
                self.away(last_seen=datetime.fromtimestamp(status.was_online))
        elif isinstance(status, tgapi.UserStatusOnline):
            self.online()
            self.active()
            self._online_expire_task.cancel()
            self._online_expire_task = self.xmpp.loop.create_task(
                self._expire_online(status.expires)
            )
        elif isinstance(status, tgapi.UserStatusRecently):
            self.inactive()
            self.away(
                "Last seen recently" if global_config.LAST_SEEN_FALLBACK else None,
                last_seen=datetime.now(),
            )

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


class Roster(LegacyRoster["Session", "Contact", int]):
    async def jid_username_to_legacy_id(self, jid_username: str) -> int:
        try:
            tg_id = int(jid_username)
        except ValueError:
            raise XMPPError("bad-request", "This is not a telegram user ID")
        else:
            if tg_id > 0:
                return tg_id
            else:
                raise XMPPError("bad-request", "This looks like a telegram group ID")


log = logging.getLogger(__name__)
