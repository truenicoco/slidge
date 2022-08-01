import asyncio
import logging
import re
import tempfile
from mimetypes import guess_type
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiohttp
import aiotdlib.api as tgapi
from slixmpp.exceptions import XMPPError

from slidge import *

if TYPE_CHECKING:
    from .client import (
        TelegramClient,  # FIXME: circular imports, probably bad module/class architecture...
    )
    from .contact import Contact, Roster
    from .gateway import Gateway


class Session(BaseSession["Contact", "Roster", "Gateway"]):
    tdlib_path: Optional[Path] = None
    tg: "TelegramClient"
    sent_read_marks: set[int]
    ack_futures: dict[int, asyncio.Future]
    user_correction_futures: dict[int, asyncio.Future]

    def post_init(self):
        registration_form = {
            k: v if v != "" else None for k, v in self.user.registration_form.items()
        }
        self.sent_read_marks = set()
        self.ack_futures = {}
        self.user_correction_futures = {}

        i = registration_form.get("api_id")
        if i is not None:
            i = int(i)  # makes testing easier to make api_id optional...

        # FIXME: circular imports, probably bad module/class architecture...
        from .client import TelegramClient
        from .gateway import Gateway

        self.tg = TelegramClient(
            self,
            api_id=i,
            api_hash=registration_form.get("api_hash"),
            phone_number=registration_form["phone"],
            bot_token=registration_form.get("bot_token"),
            first_name=registration_form.get("first"),
            last_name=registration_form.get("last"),
            database_encryption_key=Gateway.args.tdlib_key,
            files_directory=Gateway.args.tdlib_path,
        )

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        try:
            return int(i)
        except ValueError:
            raise NotImplementedError("This is not a valid telegram msg ID")

    async def login(self):
        await self.tg.start()
        await self.add_contacts_to_roster()
        return f"Connected as {self.tg.get_my_id()}"

    async def logout(self):
        await self.tg.stop()

    async def wait_for_tdlib_success(self, result_id: int):
        fut = self.xmpp.loop.create_future()
        self.ack_futures[result_id] = fut
        return await fut

    async def send_text(self, t: str, c: "Contact") -> int:
        t = escape(t)
        try:
            result = await self.tg.send_text(chat_id=c.legacy_id, text=t)
        except tgapi.BadRequest as e:
            if e.code == 400:
                raise XMPPError(condition="item-not-found", text="No such contact")
            else:
                raise
        new_message_id = await self.wait_for_tdlib_success(result.id)
        self.log.debug("Result: %s / %s", result, new_message_id)
        return new_message_id

    async def send_file(self, u: str, c: "Contact") -> int:
        type_, _ = guess_type(u)
        if type_ is not None:
            type_, subtype = type_.split("/")

        if type_ == "image":
            async with aiohttp.ClientSession() as session:
                async with session.get(u) as response:
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile() as file:
                        bytes_ = await response.read()
                        file.write(bytes_)
                        result = await self.tg.send_photo(
                            chat_id=c.legacy_id, photo=file.name
                        )
        else:
            result = await self.tg.send_text(chat_id=c.legacy_id, text=u)

        return result.id

    async def active(self, c: "Contact"):
        action = tgapi.OpenChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        self.log.debug("Open chat res: %s", res)

    async def inactive(self, c: "Contact"):
        action = tgapi.CloseChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        self.log.debug("Close chat res: %s", res)

    async def composing(self, c: "Contact"):
        action = tgapi.SendChatAction.construct(
            chat_id=c.legacy_id,
            action=tgapi.ChatActionTyping(),
            message_thread_id=0,  # TODO: check what telegram's threads really are
        )

        res = await self.tg.request(action)
        self.log.debug("Send composing res: %s", res)

    async def paused(self, c: "Contact"):
        pass

    async def displayed(self, tg_id: int, c: "Contact"):
        query = tgapi.ViewMessages.construct(
            chat_id=c.legacy_id,
            message_thread_id=0,
            message_ids=[tg_id],
            force_read=True,
        )
        res = await self.tg.request(query)
        self.log.debug("Send chat action res: %s", res)

    async def add_contacts_to_roster(self):
        chats = await self.tg.get_main_list_chats_all()
        for chat in chats:
            if not isinstance(chat.type_, tgapi.ChatTypePrivate):
                self.log.debug(
                    "Skipping %s as it is of type %s", chat.title, chat.type_
                )
            if isinstance(chat.photo, tgapi.ChatPhotoInfo):
                query = tgapi.DownloadFile.construct(
                    file_id=chat.photo.big.id, synchronous=True, priority=32
                )
                response: tgapi.File = await self.tg.request(query)
                with open(response.local.path, "rb") as f:
                    avatar = f.read()
            else:
                avatar = None
            contact = self.contacts.by_legacy_id(chat.id)
            contact.name = chat.title
            contact.avatar = avatar
            await contact.add_to_roster()
            contact.online()

    async def correct(self, text: str, legacy_msg_id: int, c: "Contact"):
        f = self.user_correction_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        query = tgapi.EditMessageText.construct(
            chat_id=c.legacy_id,
            message_id=legacy_msg_id,
            input_message_content=tgapi.InputMessageText.construct(
                text=tgapi.FormattedText.construct(text=text)
            ),
        )
        await self.tg.request(query)
        await f

    async def search(self, form_values: dict[str, str]):
        phone = form_values["phone"]
        response: tgapi.ImportedContacts = await self.tg.request(
            query=tgapi.ImportContacts(
                contacts=[
                    tgapi.Contact(
                        phone_number=phone,
                        user_id=0,
                        first_name=phone,
                        vcard="",
                        last_name="",
                    )
                ]
            )
        )
        user_id = response.user_ids[0]
        if user_id == 0:
            return

        await self.add_contacts_to_roster()
        contact = self.contacts.by_legacy_id(user_id)
        await contact.add_to_roster()

        return SearchResult(
            fields=[FormField("phone"), FormField("jid", type="jid-single")],
            items=[{"phone": form_values["phone"], "jid": contact.jid.bare}],
        )


async def on_message_success(
    tg: "TelegramClient", update: tgapi.UpdateMessageSendSucceeded
):
    tg.session.sent_read_marks.add(update.message.id)
    for _ in range(10):
        try:
            future = tg.session.ack_futures.pop(update.message.id)
        except KeyError:
            await asyncio.sleep(0.5)
        else:
            future.set_result(update.message.id)
            return
    log.warning("Ignoring Send success for %s", update.message.id)


def escape(t: str):
    return re.sub(ESCAPE_PATTERN, r"\\\1", t)


RESERVED_CHARS = "_*[]()~`>#+-=|{}.!"
ESCAPE_PATTERN = re.compile(f"([{re.escape(RESERVED_CHARS)}])")

log = logging.getLogger(__name__)
