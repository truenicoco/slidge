import asyncio
import datetime
import functools
import logging
import re
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Dict, List, Optional
from mimetypes import guess_type

import aiohttp
from slixmpp import JID, Presence
from slixmpp.exceptions import XMPPError

import aiotdlib
import aiotdlib.api as tgapi

from slidge import *
from .config import get_parser

REGISTRATION_INSTRUCTIONS = """You can visit https://my.telegram.org/apps to get an API ID and an API HASH

This is the only tested login method, but other methods (password, bot token, 2FA...)
should work too, in theory at least.
"""


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
        FormField(var="api_id", label="API ID", required=False),
        FormField(var="api_hash", label="API hash", required=False),
        FormField(var="", value="The fields below have not been tested", type="fixed"),
        FormField(var="bot_token", label="Bot token", required=False),
        FormField(var="first", label="First name", required=False),
        FormField(var="last", label="Last name", required=False),
    ]
    ROSTER_GROUP = "Telegram"
    COMPONENT_NAME = "Telegram (slidge)"
    COMPONENT_TYPE = "telegram"
    COMPONENT_AVATAR = "https://web.telegram.org/img/logo_share.png"

    SEARCH_FIELDS = [
        FormField(var="phone", label="Phone number", required=True),
    ]

    args: Namespace

    def config(self, argv: List[str]):
        Gateway.args = args = get_parser().parse_args(argv)
        if args.tdlib_path is None:
            args.tdlib_path = self.home_dir / "tdlib"

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        pass

    async def unregister(self, user):
        pass


class Contact(LegacyContact):
    legacy_id: int


class Roster(LegacyRoster):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


class Session(BaseSession):
    tdlib_path: Optional[Path] = None
    tg: "TelegramClient"
    sent_read_marks: set[int]

    def post_init(self):
        registration_form = {
            k: v if v != "" else None for k, v in self.user.registration_form.items()
        }
        self.sent_read_marks = set()

        i = registration_form.get("api_id")
        if i is not None:
            i = int(i)  # makes testing easier to make api_id optional...

        self.tg = TelegramClient(
            self.xmpp,
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

    async def login(self, p: Presence):
        self.send_gateway_status("Connecting", show="dnd")
        async with self.tg as tg:
            self.send_gateway_status(f"Connected as {self.tg.get_my_id()}")
            await self.add_contacts_to_roster()
            await tg.idle()

    async def logout(self, p: Optional[Presence]):
        pass

    async def send_text(self, t: str, c: Contact) -> int:
        t = escape(t)
        try:
            result = await self.tg.send_text(chat_id=c.legacy_id, text=t)
        except tgapi.BadRequest as e:
            if e.code == 400:
                raise XMPPError(condition="item-not-found", text="No such contact")
            else:
                raise
        fut = self.xmpp.loop.create_future()
        ack_futures[result.id] = fut
        new_message_id = await fut
        log.debug("Result: %s / %s", result, new_message_id)
        return new_message_id

    async def send_file(self, u: str, c: Contact) -> int:
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

    async def active(self, c: Contact):
        action = tgapi.OpenChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        log.debug("Open chat res: %s", res)

    async def inactive(self, c: Contact):
        action = tgapi.CloseChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        log.debug("Close chat res: %s", res)

    async def composing(self, c: Contact):
        action = tgapi.SendChatAction.construct(
            chat_id=c.legacy_id,
            action=tgapi.ChatActionTyping(),
            message_thread_id=0,  # TODO: check what telegram's threads really are
        )

        res = await self.tg.request(action)
        log.debug("Send composing res: %s", res)

    async def paused(self, c: Contact):
        pass

    async def displayed(self, tg_id: int, c: Contact):
        query = tgapi.ViewMessages.construct(
            chat_id=c.legacy_id,
            message_thread_id=0,
            message_ids=[tg_id],
            force_read=True,
        )
        res = await self.tg.request(query)
        log.debug("Send chat action res: %s", res)

    async def add_contacts_to_roster(self):
        chats = await self.tg.get_main_list_chats_all()
        for chat in chats:
            if not isinstance(chat.type_, tgapi.ChatTypePrivate):
                log.debug("Skipping %s as it is of type %s", chat.title, chat.type_)
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

    async def correct(self, text: str, legacy_msg_id: int, c: Contact):
        query = tgapi.EditMessageText.construct(
            chat_id=c.legacy_id,
            message_id=legacy_msg_id,
            input_message_content=tgapi.InputMessageText.construct(
                text=tgapi.FormattedText.construct(text=text)
            ),
        )
        await self.tg.request(query)

    async def search(self, form_values: Dict[str, str]):
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
        contact: Contact = self.contacts.by_legacy_id(user_id)
        await contact.add_to_roster()

        return SearchResult(
            fields=[FormField("phone"), FormField("jid", type="jid-single")],
            items=[{"phone": form_values["phone"], "jid": contact.jid.bare}],
        )


class TelegramClient(aiotdlib.Client):
    def __init__(self, xmpp: BaseGateway, session: Session, **kw):
        super().__init__(parse_mode=aiotdlib.ClientParseMode.MARKDOWN, **kw)
        self.session = session

        async def input_(prompt):
            self.session.send_gateway_status(f"Action required: {prompt}")
            return await xmpp.input(session.user, prompt)

        self.input = input_
        self._auth_get_code = functools.partial(input_, "Enter code")
        self._auth_get_password = functools.partial(input_, "Enter 2FA password:")
        self._auth_get_first_name = functools.partial(input_, "Enter first name:")
        self._auth_get_last_name = functools.partial(input_, "Enter last name:")

        for h, t in [
            (on_telegram_message, tgapi.API.Types.UPDATE_NEW_MESSAGE),
            (on_message_success, tgapi.API.Types.UPDATE_MESSAGE_SEND_SUCCEEDED),
            (on_contact_status, tgapi.API.Types.UPDATE_USER_STATUS),
            (on_contact_chat_action, tgapi.API.Types.UPDATE_CHAT_ACTION),
            (on_contact_read, tgapi.API.Types.UPDATE_CHAT_READ_OUTBOX),
            (on_user_read_from_other_device, tgapi.API.Types.UPDATE_CHAT_READ_INBOX),
            (on_contact_edit_msg, tgapi.API.Types.UPDATE_MESSAGE_CONTENT),
            (on_user_update, tgapi.API.Types.UPDATE_USER),
        ]:
            self.add_event_handler(h, t)


async def on_telegram_message(tg: TelegramClient, update: tgapi.UpdateNewMessage):
    log.debug("Received message update")
    msg: tgapi.Message = update.message
    session = tg.session

    if msg.is_channel_post:
        log.debug("Ignoring channel post")
        return

    if msg.is_outgoing:
        # This means slidge is responsible for this message, so no carbon is needed;
        # but maybe this does not handle all possible cases gracefully?
        if msg.sending_state is not None or msg.id in session.sent:
            return
        contact = session.contacts.by_legacy_id(msg.chat_id)
        # noinspection PyUnresolvedReferences
        contact.carbon(
            msg.content.text.text, msg.id, datetime.datetime.fromtimestamp(msg.date)
        )
        return

    sender = msg.sender_id
    if not isinstance(sender, tgapi.MessageSenderUser):
        log.debug("Ignoring non-user sender")  # Does this happen?
        return

    contact = session.contacts.by_legacy_id(sender.user_id)

    content = msg.content
    if isinstance(content, tgapi.MessageText):
        # TODO: parse formatted text to markdown
        formatted_text = content.text
        contact.send_text(body=formatted_text.text, legacy_msg_id=msg.id)
        return

    if isinstance(content, tgapi.MessageAnimatedEmoji):
        emoji = content.animated_emoji.sticker.emoji
        contact.send_text(body=emoji, legacy_msg_id=msg.id)
        return

    if isinstance(content, tgapi.MessagePhoto):
        photo = content.photo
        best_file = max(photo.sizes, key=lambda x: x.width).photo
    elif isinstance(content, tgapi.MessageVideo):
        best_file = content.video.video
    else:
        raise NotImplemented

    query = tgapi.DownloadFile.construct(
        file_id=best_file.id, synchronous=True, priority=1
    )
    best_file_downloaded: tgapi.File = await tg.request(query)
    await contact.send_file(best_file_downloaded.local.path)
    if content.caption.text:
        contact.send_text(content.caption.text, legacy_msg_id=msg.id)


async def on_message_success(
    tg: TelegramClient, update: tgapi.UpdateMessageSendSucceeded
):
    tg.session.sent_read_marks.add(update.message.id)
    for _ in range(10):
        try:
            future = ack_futures.pop(update.message.id)
        except KeyError:
            await asyncio.sleep(0.5)
        else:
            future.set_result(update.message.id)
            return
    log.warning("Ignoring Send success for %s", update.message.id)


async def on_contact_status(tg: TelegramClient, update: tgapi.UpdateUserStatus):
    if update.user_id == await tg.get_my_id():
        return

    session = tg.session
    contact = session.contacts.by_legacy_id(update.user_id)
    status = update.status
    if isinstance(status, tgapi.UserStatusOnline):
        contact.active()
    elif isinstance(status, tgapi.UserStatusOffline):
        contact.paused()
        contact.inactive()
    else:
        log.debug("Ignoring status %s", update)


async def on_contact_read(tg: TelegramClient, update: tgapi.UpdateChatReadOutbox):
    tg.session.contacts.by_legacy_id(update.chat_id).displayed(
        update.last_read_outbox_message_id
    )


async def on_contact_chat_action(tg: TelegramClient, action: tgapi.UpdateChatAction):
    session = tg.session
    sender = action.sender_id
    if not isinstance(sender, tgapi.MessageSenderUser):
        log.debug("Ignoring action: %s", action)
        return

    chat_id = action.chat_id
    if chat_id != sender.user_id:
        log.debug("Ignoring action: %s", action)
        return
    contact = session.contacts.by_legacy_id(chat_id)
    contact.composing()


async def on_user_read_from_other_device(
    tg: TelegramClient, action: tgapi.UpdateChatReadInbox
):
    session = tg.session
    msg_id = action.last_read_inbox_message_id
    log.debug("Self read mark for %s and we sent %s", msg_id, session.sent_read_marks)
    try:
        session.sent_read_marks.remove(msg_id)
    except KeyError:
        # slidge didn't send this read mark, so it comes from the official tg client
        contact = session.contacts.by_legacy_id(action.chat_id)
        contact.carbon_read(msg_id)


async def on_contact_edit_msg(tg: TelegramClient, action: tgapi.UpdateMessageContent):
    new = action.new_content
    if not isinstance(new, tgapi.MessageText):
        raise NotImplementedError(new)
    session = tg.session
    contact = session.contacts.by_legacy_id(action.chat_id)
    contact.correct(action.message_id, new.text.text)


async def on_user_update(tg: TelegramClient, action: tgapi.UpdateUser):
    u = action.user
    if u.id == await tg.get_my_id():
        return
    await tg.request(
        query=tgapi.ImportContacts(
            contacts=[
                tgapi.Contact(
                    phone_number=u.phone_number,
                    user_id=u.id,
                    first_name=u.first_name,
                    last_name=u.last_name,
                    vcard="",
                )
            ]
        )
    )
    contact: Contact = tg.session.contacts.by_legacy_id(u.id)
    contact.name = u.first_name
    await contact.add_to_roster()


def escape(t: str):
    return re.sub(ESCAPE_PATTERN, r"\\\1", t)


RESERVED_CHARS = "_*[]()~`>#+-=|{}.!"
ESCAPE_PATTERN = re.compile(f"([{re.escape(RESERVED_CHARS)}])")


ack_futures: Dict[int, asyncio.Future] = {}
log = logging.getLogger(__name__)
