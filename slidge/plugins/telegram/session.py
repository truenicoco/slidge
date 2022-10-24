import asyncio
import logging
import re
import tempfile
from mimetypes import guess_type
from pathlib import Path
from typing import Optional

import aiohttp
import aiotdlib.api as tgapi
from aiotdlib.api.errors import BadRequest
from slixmpp.exceptions import XMPPError

from slidge import *

from .client import TelegramClient
from .contact import Contact, Roster
from .gateway import Gateway


class Session(BaseSession[Contact, Roster, Gateway]):
    tdlib_path: Optional[Path] = None
    tg: TelegramClient
    sent_read_marks: set[int]
    ack_futures: dict[int, asyncio.Future]
    user_correction_futures: dict[int, asyncio.Future]
    delete_futures: dict[int, asyncio.Future]

    def post_init(self):
        registration_form = {
            k: v if v != "" else None for k, v in self.user.registration_form.items()
        }
        self.sent_read_marks = set()
        self.ack_futures = {}
        self.user_correction_futures = {}
        self.delete_futures = {}

        i = registration_form.get("api_id")
        if i is not None:
            i = int(i)  # makes testing easier to make api_id optional...

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
        return f"Connected as {await self.tg.get_my_id()}"

    async def logout(self):
        await self.tg.stop()

    async def wait_for_tdlib_success(self, result_id: int):
        fut = self.xmpp.loop.create_future()
        self.ack_futures[result_id] = fut
        return await fut

    async def send_text(self, t: str, c: "Contact", *, reply_to_msg_id=None) -> int:
        t = escape(t)
        try:
            result = await self.tg.send_text(
                chat_id=c.legacy_id, text=t, reply_to_message_id=reply_to_msg_id
            )
        except tgapi.BadRequest as e:
            if e.code == 400:
                raise XMPPError(condition="item-not-found", text="No such contact")
            else:
                raise
        new_message_id = await self.wait_for_tdlib_success(result.id)
        self.log.debug("Result: %s / %s", result, new_message_id)
        return new_message_id

    async def send_file(self, u: str, c: "Contact", *, reply_to_msg_id=None) -> int:
        type_, _ = guess_type(u)
        if type_ is not None:
            type_, subtype = type_.split("/")

        async with aiohttp.ClientSession() as session:
            async with session.get(u) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile() as file:
                    bytes_ = await response.read()
                    file.write(bytes_)
                    if type_ == "image":
                        result = await self.tg.send_photo(
                            chat_id=c.legacy_id, photo=file.name
                        )
                    elif type_ == "video":
                        result = await self.tg.send_video(
                            chat_id=c.legacy_id, video=file.name
                        )
                    else:
                        result = await self.tg.send_document(
                            c.legacy_id, document=file.name
                        )

        return result.id

    async def active(self, c: "Contact"):
        res = await self.tg.api.open_chat(chat_id=c.legacy_id)
        self.log.debug("Open chat res: %s", res)

    async def inactive(self, c: "Contact"):
        res = await self.tg.api.close_chat(chat_id=c.legacy_id)
        self.log.debug("Close chat res: %s", res)

    async def composing(self, c: "Contact"):
        res = await self.tg.api.send_chat_action(
            chat_id=c.legacy_id,
            action=tgapi.ChatActionTyping(),
            message_thread_id=0,  # TODO: check what telegram's threads really are
        )
        self.log.debug("Send composing res: %s", res)

    async def paused(self, c: "Contact"):
        pass

    async def displayed(self, tg_id: int, c: "Contact"):
        res = await self.tg.api.view_messages(
            chat_id=c.legacy_id,
            message_thread_id=0,
            message_ids=[tg_id],
            force_read=True,
        )
        self.log.debug("Send chat action res: %s", res)

    async def add_contacts_to_roster(self):
        users = await self.tg.api.get_contacts()
        for id_ in users.user_ids:
            contact = self.contacts.by_legacy_id(id_)
            await contact.add_to_roster()
            await contact.update_info_from_user()

    async def correct(self, text: str, legacy_msg_id: int, c: "Contact"):
        f = self.user_correction_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        await self.tg.api.edit_message_text(
            chat_id=c.legacy_id,
            message_id=legacy_msg_id,
            reply_markup=None,
            input_message_content=tgapi.InputMessageText.construct(
                text=tgapi.FormattedText.construct(text=text)
            ),
        )
        await f

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

        await self.add_contacts_to_roster()
        contact = self.contacts.by_legacy_id(user_id)
        await contact.update_info_from_user()
        await contact.add_to_roster()

        return SearchResult(
            fields=[FormField("phone"), FormField("jid", type="jid-single")],
            items=[{"phone": form_values["phone"], "jid": contact.jid.bare}],
        )

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

    async def react(self, legacy_msg_id, emojis, c: "Contact"):
        if len(emojis) == 0:
            await self.remove_reactions(legacy_msg_id, c)
            return

        if len(emojis) > 1:
            c.carbon_react(legacy_msg_id)
            await self.remove_reactions(legacy_msg_id, c)
            self.send_gateway_message(
                "Warning: unlike XMPP, telegram only accepts one reaction per message. "
                f"Your reactions have been removed."
            )
            return

        emoji = emojis[-1]

        try:
            r = await self.tg.api.set_message_reaction(
                chat_id=c.legacy_id,
                message_id=legacy_msg_id,
                reaction=emoji,
                is_big=False,
            )
        except BadRequest as e:
            available = await self.tg.api.get_message_available_reactions(
                chat_id=c.legacy_id, message_id=legacy_msg_id
            )
            available_emojis = [a.reaction for a in available.reactions]
            self.send_gateway_message(
                "Error: unlike XMPP, telegram does not allow arbitrary emojis to be used as reactions: "
                f"{e.message}. Please pick your reaction in this list: {' '.join(available_emojis)}"
            )
            c.carbon_react(legacy_msg_id)
        else:
            self.log.debug("Message reaction response: %s", r)

    async def retract(self, legacy_msg_id, c):
        f = self.delete_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        r = await self.tg.api.delete_messages(c.legacy_id, [legacy_msg_id], revoke=True)
        self.log.debug("Delete message response: %s", r)
        confirmation = await f
        self.log.debug("Message delete confirmation: %s", confirmation)


def escape(t: str):
    return re.sub(ESCAPE_PATTERN, r"\\\1", t)


RESERVED_CHARS = "_*[]()~`>#+-=|{}.!"
ESCAPE_PATTERN = re.compile(f"([{re.escape(RESERVED_CHARS)}])")

log = logging.getLogger(__name__)
