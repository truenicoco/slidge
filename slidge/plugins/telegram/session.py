import asyncio
import functools
import logging
import re
import tempfile
from typing import Union

import aiotdlib.api as tgapi
from aiotdlib.api.errors import BadRequest

from slidge import *

from ...util.types import Chat
from . import config
from .client import TelegramClient
from .contact import Contact, Roster
from .gateway import Gateway
from .group import MUC


def catch_chat_not_found(coroutine):
    @functools.wraps(coroutine)
    async def wrapped(self: "Session", *a, **k):
        try:
            return await coroutine(self, *a, **k)
        except tgapi.BadRequest as e:
            if e.code == 400:
                # FIXME: Chat should always be the first arg for a cleaner API...
                if len(a) == 1:
                    chat: Chat = a[0]
                elif len(a) == 2:
                    chat = a[1]
                else:
                    chat = k.get("chat", k.get("c"))
                if chat is None:
                    raise RuntimeError(a, k)
                try:
                    await self.tg.api.create_private_chat(chat.legacy_id, False)
                except tgapi.BadRequest as e2:
                    if e.code == 400:
                        raise XMPPError(condition="item-not-found", text=e2.message)
                    else:
                        raise XMPPError(
                            condition="internal-server-error", text=e2.message
                        )
            else:
                raise XMPPError(condition="internal-server-error", text=e.message)
            return await coroutine(self, *a, **k)

    return wrapped


class Session(
    BaseSession[Gateway, int, Roster, Contact, LegacyBookmarks, MUC, LegacyParticipant]
):
    def __init__(self, user):
        super().__init__(user)
        self.sent_read_marks = set[int]()
        self.ack_futures = dict[int, asyncio.Future]()
        self.user_correction_futures = dict[int, asyncio.Future]()
        self.delete_futures = dict[int, asyncio.Future]()

        self.tg = TelegramClient(self)

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> int:
        return int(i)

    async def login(self):
        await self.tg.start()
        me = await self.tg.get_user(await self.tg.get_my_id())
        my_name = (me.first_name + " " + me.last_name).strip()
        self.bookmarks.user_nick = my_name
        return f"Connected as {my_name}"

    async def logout(self):
        await self.tg.stop()

    async def wait_for_tdlib_success(self, result_id: int):
        fut = self.xmpp.loop.create_future()
        self.ack_futures[result_id] = fut
        return await fut

    @catch_chat_not_found
    async def send_text(
        self,
        text: str,
        chat: Union[Contact, MUC],
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
        reply_to=None,
        **kwargs,
    ) -> int:
        text = escape(text)
        result = await self.tg.send_text(
            chat_id=chat.legacy_id, text=text, reply_to_message_id=reply_to_msg_id
        )
        new_message_id = await self.wait_for_tdlib_success(result.id)
        self.log.debug("Result: %s / %s", result, new_message_id)
        return new_message_id

    @catch_chat_not_found
    async def send_file(
        self, url: str, chat: Chat, http_response, reply_to_msg_id=None, **_
    ) -> int:
        type_, _subtype = http_response.content_type.split("/")
        kwargs = dict(chat_id=chat.legacy_id, reply_to_message_id=reply_to_msg_id)
        stickers_pattern = config.OUTGOING_STICKERS_REGEXP
        with tempfile.NamedTemporaryFile() as file:
            bytes_ = await http_response.read()
            file.write(bytes_)
            if stickers_pattern and re.match(stickers_pattern, url.split("/")[-1]):
                result = await self.tg.send_sticker(sticker=file.name, **kwargs)
            elif type_ == "image":
                result = await self.tg.send_photo(photo=file.name, **kwargs)
            elif type_ == "video":
                result = await self.tg.send_video(video=file.name, **kwargs)
            elif type_ == "audio":
                result = await self.tg.send_audio(audio=file.name, **kwargs)
            else:
                result = await self.tg.send_document(document=file.name, **kwargs)

        return result.id

    @catch_chat_not_found
    async def active(self, c: "Contact"):
        res = await self.tg.api.open_chat(chat_id=c.legacy_id)
        self.log.debug("Open chat res: %s", res)

    @catch_chat_not_found
    async def inactive(self, c: "Contact"):
        res = await self.tg.api.close_chat(chat_id=c.legacy_id)
        self.log.debug("Close chat res: %s", res)

    @catch_chat_not_found
    async def composing(self, c: "Contact"):
        res = await self.tg.api.send_chat_action(
            chat_id=c.legacy_id,
            action=tgapi.ChatActionTyping(),
            message_thread_id=0,  # TODO: check what telegram's threads really are
        )
        self.log.debug("Send composing res: %s", res)

    @catch_chat_not_found
    async def paused(self, c: "Contact"):
        pass

    @catch_chat_not_found
    async def displayed(self, tg_id: int, c: "Contact"):
        res = await self.tg.api.view_messages(
            chat_id=c.legacy_id,
            message_thread_id=0,
            message_ids=[tg_id],
            force_read=True,
        )
        self.log.debug("Send chat action res: %s", res)

    @catch_chat_not_found
    async def correct(self, text: str, legacy_msg_id: int, c: "Contact"):
        f = self.user_correction_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        await self.tg.api.edit_message_text(
            chat_id=c.legacy_id,
            message_id=legacy_msg_id,
            reply_markup=None,
            input_message_content=tgapi.InputMessageText.construct(
                text=tgapi.FormattedText.construct(text=text)
            ),
            skip_validation=True,
        )
        await f

    @catch_chat_not_found
    async def search(self, form_values: dict[str, str]):
        phone = form_values["phone"]
        first = form_values.get("first", phone)
        last = form_values.get("last", "")
        response = await self.tg.api.import_contacts(
            contacts=[
                tgapi.Contact(
                    phone_number=phone,
                    user_id=0,
                    first_name=first,
                    vcard="",
                    last_name=last,
                )
            ]
        )
        user_id = response.user_ids[0]
        if user_id == 0:
            return

        contact = await self.contacts.by_legacy_id(user_id)
        await contact.add_to_roster()

        return SearchResult(
            fields=[FormField("phone"), FormField("jid", type="jid-single")],
            items=[{"phone": form_values["phone"], "jid": contact.jid.bare}],
        )

    @catch_chat_not_found
    async def remove_reactions(self, legacy_msg_id, c: "Contact"):
        try:
            r = await self.tg.api.set_message_reaction(
                chat_id=c.legacy_id,
                message_id=legacy_msg_id,
                reaction="",
                is_big=False,
            )
        except BadRequest as e:
            self.log.debug("Remove reaction error: %s", e)
        else:
            self.log.debug("Remove reaction response: %s", r)

    @catch_chat_not_found
    async def react(self, legacy_msg_id: int, emojis: list[str], c: "Contact"):
        if len(emojis) == 0:
            await self.remove_reactions(legacy_msg_id, c)
            return

        # we never have more than 1 emoji, slidge core makes sure of that
        try:
            r = await self.tg.api.set_message_reaction(
                chat_id=c.legacy_id,
                message_id=legacy_msg_id,
                reaction=emojis[0],
                is_big=False,
            )
        except BadRequest as e:
            raise XMPPError("bad-request", text=e.message)
        else:
            self.log.debug("Message reaction response: %s", r)

    @catch_chat_not_found
    async def retract(self, legacy_msg_id, c):
        f = self.delete_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        r = await self.tg.api.delete_messages(c.legacy_id, [legacy_msg_id], revoke=True)
        self.log.debug("Delete message response: %s", r)
        confirmation = await f
        self.log.debug("Message delete confirmation: %s", confirmation)


def escape(t: str):
    return re.sub(ESCAPE_PATTERN, r"\\\1", t)


RESERVED_CHARS = r"_*[]()~`>#+-=|{}.!\\"
ESCAPE_PATTERN = re.compile(f"([{re.escape(RESERVED_CHARS)}])")

log = logging.getLogger(__name__)
