import datetime
import functools
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Dict, Optional, List

from slixmpp import Message, JID, Presence, Iq

import aiotdlib
import aiotdlib.api as tgapi

from slidge import *

REGISTRATION_INSTRUCTIONS = """You can visit https://my.telegram.org/apps to get an API ID and an API HASH

This is the only tested login method, but other methods (password, bot token, 2FA...)
should work too, in theory at least.
"""


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = REGISTRATION_INSTRUCTIONS
    REGISTRATION_FIELDS = [
        RegistrationField(name="phone", label="Phone number", required=True),
        RegistrationField(name="api_id", label="API ID", required=False),
        RegistrationField(name="api_hash", label="API hash", required=False),
        RegistrationField(
            name="", value="The fields below have not been tested", type="fixed"
        ),
        RegistrationField(name="bot_token", label="Bot token", required=False),
        RegistrationField(name="first", label="First name", required=False),
        RegistrationField(name="last", label="Last name", required=False),
    ]

    ROSTER_GROUP = "Telegram"

    COMPONENT_NAME = "Telegram (slidge)"


class LegacyClient(BaseLegacyClient):
    def config(self, argv: List[str]):
        parser = ArgumentParser()
        parser.add_argument("--tdlib-path")
        args = parser.parse_args(argv)
        if args.tdlib_path is not None:
            Session.tdlib_files = Path(args.tdlib_path)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        pass

    async def unregister(self, user: GatewayUser, iq: Iq):
        pass


class Session(BaseSession):
    tdlib_path = Path("/tdlib")
    tg: "TelegramClient"

    def post_init(self):
        registration_form = {
            k: v if v != "" else None for k, v in self.user.registration_form.items()
        }
        self.tg = TelegramClient(
            self.xmpp,
            self,
            api_id=int(registration_form["api_id"]),
            api_hash=registration_form["api_hash"],
            phone_number=registration_form["phone"],
            bot_token=registration_form["bot_token"],
            first_name=registration_form["first"],
            last_name=registration_form["last"],
            database_encryption_key="USELESS",
            files_directory=Session.tdlib_path,
        )

    async def login(self, p: Presence):
        async with self.tg as tg:
            self.logged = True
            await self.add_contacts_to_roster()
            await tg.idle()

    async def logout(self, p: Presence):
        pass

    async def send(self, m: Message, c: LegacyContact) -> int:
        # noinspection PyTypeChecker
        # ^ because c.legacy_id is of general type Hashable, but it's an int for telegram
        result = await self.tg.send_text(chat_id=c.legacy_id, text=m["body"])
        log.debug("Result: %s", result)
        return result.id

    async def active(self, c: LegacyContact):
        action = tgapi.OpenChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        log.debug("Open chat res: %s", res)

    async def inactive(self, c: LegacyContact):
        action = tgapi.CloseChat.construct(chat_id=c.legacy_id)
        res = await self.tg.request(action)
        log.debug("Close chat res: %s", res)

    async def composing(self, c: LegacyContact):
        action = tgapi.SendChatAction.construct(
            chat_id=c.legacy_id,
            action=tgapi.ChatActionTyping(),
            message_thread_id=0,  # TODO: check what telegram's threads really are
        )

        res = await self.tg.request(action)
        log.debug("Send composing res: %s", res)

    async def displayed(self, tg_id: int, c: LegacyContact):
        log.debug("Unread: %s", self.unread_by_user)
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
            log.debug("Photo: %s - %s", chat.photo, type(chat.photo))
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


class TelegramClient(aiotdlib.Client):
    def __init__(self, xmpp: BaseGateway, session: Session, **kw):
        super().__init__(**kw)
        self.session = session

        async def input_(prompt):
            return await xmpp.input(session.user, prompt)

        self.input = input_
        self.__auth_get_code = functools.partial(input_, "Enter code")
        self.__auth_get_password = functools.partial(input_, "Enter 2FA password:")
        self.__auth_get_first_name = functools.partial(input_, "Enter first name:")
        self.__auth_get_last_name = functools.partial(input_, "Enter last name:")

        for h, t in [
            (on_telegram_message, tgapi.API.Types.UPDATE_NEW_MESSAGE),
            (on_message_success, tgapi.API.Types.UPDATE_MESSAGE_SEND_SUCCEEDED),
            (on_contact_status, tgapi.API.Types.UPDATE_USER_STATUS),
            (on_contact_chat_action, tgapi.API.Types.UPDATE_CHAT_ACTION),
            (on_contact_read, tgapi.API.Types.UPDATE_CHAT_READ_OUTBOX),
        ]:
            log.debug("Adding telegram event handlers")
            self.add_event_handler(h, t)


async def on_telegram_message(tg: TelegramClient, update: tgapi.UpdateNewMessage):
    log.debug("Telegram update: %s", update)
    msg: tgapi.Message = update.message
    session = tg.session

    if msg.is_channel_post:
        log.debug("Ignoring channel post")
        return

    if msg.is_outgoing:
        # This means slidge is responsible for this message, so no carbon is needed;
        # but maybe this does not handle all possible cases gracefully?
        if (
            msg.sending_state is not None
            or msg.id in session.unacked
            or msg.id in session.unread
        ):

            return
        contact = session.contacts.by_legacy_id(msg.chat_id)
        # noinspection PyUnresolvedReferences
        contact.carbon(msg.content.text.text, datetime.datetime.fromtimestamp(msg.date))
        return

    sender = msg.sender_id
    if not isinstance(sender, tgapi.MessageSenderUser):
        log.debug("Ignoring non-user sender")  # Does this happen?
        return

    # noinspection PyUnresolvedReferences
    contact = session.contacts.by_legacy_id(msg.sender_id.user_id)
    # noinspection PyUnresolvedReferences
    contact.send_message(body=msg.content.text.text, legacy_msg_id=msg.id)


async def on_message_success(
    tg: TelegramClient, update: tgapi.UpdateMessageSendSucceeded
):
    session = tg.session
    try:
        msg = session.unacked.pop(update.message.id)
    except KeyError:
        log.debug("We did not send: %s", update.message.id)
    else:
        session.xmpp.ack(msg)


async def on_contact_status(tg: TelegramClient, update: tgapi.UpdateUserStatus):
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
    session = tg.session
    try:
        msg = session.unread.pop(update.last_read_outbox_message_id)
    except KeyError:
        log.debug("Ignoring read mark for %s", update)
    else:
        contact = session.contacts.by_legacy_id(update.chat_id)
        contact.displayed(msg)


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


log = logging.getLogger(__name__)
