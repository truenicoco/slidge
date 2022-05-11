import datetime
import logging
from pathlib import Path
from typing import Dict, Optional

from slixmpp import Message, JID, Presence
from slixmpp.thirdparty import OrderedSet

import aiotdlib
import aiotdlib.api as tgapi

from slidge import user_store, BaseGateway, GatewayUser, BaseLegacyClient, LegacyContact


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = """
Visit https://my.telegram.org/apps to get API ID (=zip) and HASH (=first name)
"""
    REGISTRATION_FIELDS = OrderedSet(["phone", "zip", "first"])
    """Here we abuse the authorized registration fields to get the relevant info..."""

    ROSTER_GROUP = "Telegram"

    COMPONENT_NAME = "Telegram (slidge)"


class TelegramSession(aiotdlib.Client):
    def __init__(self, xmpp: BaseGateway, user: GatewayUser, **kwargs):
        super().__init__(**kwargs)
        self.xmpp = xmpp
        self.user = user
        self.unacked: Dict[int, Message] = {}
        self.unread: Dict[int, Message] = {}
        self.unread_by_user: Dict[str, int] = {}
        self.contacts: Dict[int, LegacyContact] = {}
        self.connected = False

        self.add_event_handler(
            on_telegram_message,
            tgapi.API.Types.UPDATE_NEW_MESSAGE,
        )
        self.add_event_handler(
            on_message_success,
            tgapi.API.Types.UPDATE_MESSAGE_SEND_SUCCEEDED,
        )
        self.add_event_handler(
            on_contact_status,
            tgapi.API.Types.UPDATE_USER_STATUS,
        )
        self.add_event_handler(
            on_contact_chat_action,
            tgapi.API.Types.UPDATE_CHAT_ACTION,
        )
        self.add_event_handler(
            on_contact_read,
            tgapi.API.Types.UPDATE_CHAT_READ_OUTBOX,
        )

    async def __auth_get_code(self) -> str:
        return await self.xmpp.input(self.user, "Enter code")

    async def add_contacts_to_roster(self):
        chats = await self.get_main_list_chats_all()
        for chat in chats:
            if not isinstance(chat.type_, tgapi.ChatTypePrivate):
                log.debug("Skipping %s as it is of type %s", chat.title, chat.type_)
            log.debug("Photo: %s - %s", chat.photo, type(chat.photo))
            if isinstance(chat.photo, tgapi.ChatPhotoInfo):
                query = tgapi.DownloadFile.construct(
                    file_id=chat.photo.big.id, synchronous=True, priority=32
                )
                response: tgapi.File = await self.request(query)
                with open(response.local.path, "rb") as f:
                    avatar = f.read()
            else:
                avatar = None
            contact = self.contact(chat.id, chat.title, avatar)
            await contact.add_to_roster()
            contact.online()

    def contact(
        self,
        contact_user_id: int,
        name: Optional[str] = None,
        avatar: Optional[bytes] = None,
    ) -> LegacyContact:
        c = self.contacts.get(contact_user_id)
        if c is None:
            self.contacts[contact_user_id] = c = LegacyContact(
                self.user, legacy_id=str(contact_user_id), name=name, avatar=avatar
            )

        return c


class LegacyClient(BaseLegacyClient):
    def __init__(self, xmpp: Gateway):
        super().__init__(xmpp)
        self.xmpp.add_event_handler("marker_displayed", on_user_displayed)
        self.xmpp.add_event_handler("chatstate_active", on_user_active)
        self.xmpp.add_event_handler("chatstate_inactive", on_user_inactive)
        self.xmpp.add_event_handler("chatstate_composing", on_user_composing)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        pass

    async def login(self, p: Presence):
        user = user_store.get_by_stanza(p)
        if user is None:
            raise KeyError(p.get_from().bare)
        tg = sessions.get(user)
        if tg is None:
            registration_form = user.registration_form
            tg = TelegramSession(
                self.xmpp,
                user,
                api_id=int(registration_form["zip"]),
                api_hash=registration_form["first"],
                phone_number=registration_form["phone"],
                database_encryption_key="USELESS",
                files_directory=Path("/tdlib"),
            )
            tg.connected = True
            sessions[user] = tg
            async with tg:
                await tg.add_contacts_to_roster()
                await tg.idle()

    async def logout(self, p: Presence):
        pass

    async def on_message(self, msg: Message):
        user = user_store.get_by_stanza(msg)
        tg = sessions.get(user)
        # noinspection PyTypeChecker
        result = await tg.send_text(chat_id=int(msg.get_to().user), text=msg["body"])
        tg.unacked[result.id] = msg
        tg.unread[result.id] = msg
        log.debug("Result: %s", result)


# noinspection PyUnresolvedReferences
async def on_telegram_message(client: TelegramSession, update: tgapi.UpdateNewMessage):
    log.debug("Telegram update: %s", update)
    msg: tgapi.Message = update.message

    if msg.is_channel_post:
        log.debug("Ignoring channel post")
        return

    if msg.is_outgoing:
        # This means slidge is responsible for this message, so no carbon is needed;
        # but maybe this does not handle all possible cases gracefully?
        if (
            msg.sending_state is not None
            or msg.id in client.unacked
            or msg.id in client.unread
        ):
            return
        contact = client.contact(msg.chat_id)
        contact.carbon(msg.content.text.text, datetime.datetime.fromtimestamp(msg.date))
        return

    sender = msg.sender_id
    if not isinstance(sender, tgapi.MessageSenderUser):
        log.debug("Ignoring non-user sender")  # Does this happen?
        return

    contact = client.contact(msg.sender_id.user_id)
    txt: tgapi.FormattedText = msg.content.text
    sent_msg = contact.send_message(body=txt.text)

    client.unread_by_user[sent_msg.get_id()] = msg.id


async def on_message_success(
    client: TelegramSession, update: tgapi.UpdateMessageSendSucceeded
):
    try:
        msg = client.unacked.pop(update.message.id)
    except KeyError:
        log.debug("We did not send: %s", update.message.id)
        return
    client.xmpp.ack(msg)


async def on_contact_status(client: TelegramSession, update: tgapi.UpdateUserStatus):
    contact = LegacyContact(user=client.user, legacy_id=str(update.user_id))

    status = update.status
    if isinstance(status, tgapi.UserStatusOnline):
        contact.active()
    elif isinstance(status, tgapi.UserStatusOffline):
        contact.paused()
        contact.inactive()
    else:
        log.debug("Ignoring status %s", update)


async def on_contact_read(client: TelegramSession, update: tgapi.UpdateChatReadOutbox):
    msg = client.unread.pop(update.last_read_outbox_message_id)
    if msg is None:
        log.debug("Ignoring read mark for %s", update)
        return
    contact = client.contact(update.chat_id)
    contact.displayed(msg)


async def on_contact_chat_action(
    client: TelegramSession, action: tgapi.UpdateChatAction
):
    sender = action.sender_id
    if not isinstance(sender, tgapi.MessageSenderUser):
        log.debug("Ignoring action: %s", action)
        return

    chat_id = action.chat_id
    if chat_id != sender.user_id:
        log.debug("Ignoring action: %s", action)
        return
    contact = client.contact(chat_id)
    contact.composing()


async def on_user_active(msg: Message):
    user = user_store.get_by_stanza(msg)
    tg = sessions.get(user)
    # noinspection PyTypeChecker
    chat_id = int(msg.get_to().user)
    action = tgapi.OpenChat.construct(chat_id=chat_id)
    res = await tg.request(action)
    log.debug("Open chat res: %s", res)


async def on_user_inactive(msg: Message):
    user = user_store.get_by_stanza(msg)
    tg = sessions.get(user)
    # noinspection PyTypeChecker
    chat_id = int(msg.get_to().user)
    action = tgapi.CloseChat.construct(chat_id=chat_id)
    res = await tg.request(action)
    log.debug("Open chat res: %s", res)


async def on_user_composing(msg: Message):
    user = user_store.get_by_stanza(msg)
    tg = sessions.get(user)
    # noinspection PyTypeChecker
    chat_id = int(msg.get_to().user)
    action = tgapi.SendChatAction.construct(
        chat_id=chat_id,
        action=tgapi.ChatActionTyping(),
        message_thread_id=0,  # TODO: check what telegram's threads really are
    )

    res = await tg.request(action)
    log.debug("Send chat action res: %s", res)


async def on_user_displayed(msg: Message):
    tg = sessions.get(user_store.get_by_stanza(msg))
    log.debug("Unread: %s", tg.unread_by_user)
    tg_id = tg.unread_by_user.pop(msg["displayed"]["id"])
    if tg_id is None:
        log.warning("Received read mark for a message we didn't send: %s", msg)
    # noinspection PyTypeChecker
    query = tgapi.ViewMessages.construct(
        chat_id=int(msg.get_to().user),
        message_thread_id=0,
        message_ids=[tg_id],
        force_read=True,
    )
    res = await tg.request(query)
    log.debug("Send chat action res: %s", res)


sessions: Dict[GatewayUser, TelegramSession] = {}
log = logging.getLogger(__name__)
