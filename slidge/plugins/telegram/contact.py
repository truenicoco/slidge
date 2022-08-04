import logging
from datetime import datetime
from typing import TYPE_CHECKING

import aiotdlib.api as tgapi

from slidge import *

if TYPE_CHECKING:
    from .client import TelegramClient
    from .session import Session


class Contact(LegacyContact["Session"]):
    legacy_id: int


class Roster(LegacyRoster["Contact", "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


async def on_telegram_message(tg: "TelegramClient", update: tgapi.UpdateNewMessage):
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
        contact.carbon(msg.content.text.text, msg.id, datetime.fromtimestamp(msg.date))
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


async def on_contact_status(tg: "TelegramClient", update: tgapi.UpdateUserStatus):
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


async def on_contact_read(tg: "TelegramClient", update: tgapi.UpdateChatReadOutbox):
    tg.session.contacts.by_legacy_id(update.chat_id).displayed(
        update.last_read_outbox_message_id
    )


async def on_contact_chat_action(tg: "TelegramClient", action: tgapi.UpdateChatAction):
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
    tg: "TelegramClient", action: tgapi.UpdateChatReadInbox
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


async def on_contact_edit_msg(tg: "TelegramClient", action: tgapi.UpdateMessageContent):
    new = action.new_content
    if not isinstance(new, tgapi.MessageText):
        raise NotImplementedError(new)
    session = tg.session
    try:
        fut = session.user_correction_futures.pop(action.message_id)
    except KeyError:
        contact = session.contacts.by_legacy_id(action.chat_id)
        contact.correct(action.message_id, new.text.text)
    else:
        log.debug("User correction confirmation received")
        fut.set_result(None)


async def on_user_update(tg: "TelegramClient", action: tgapi.UpdateUser):
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


async def on_msg_interaction_info(
    tg: "TelegramClient", update: tgapi.UpdateMessageInteractionInfo
):
    # FIXME: where do we filter out group chat messages here ?!
    contact = tg.session.contacts.by_legacy_id(update.chat_id)
    me = await tg.get_my_id()
    if update.interaction_info is None:
        contact.react(update.message_id, [])
    else:
        for reaction in update.interaction_info.reactions:
            for sender in reaction.recent_sender_ids:
                if isinstance(sender, tgapi.MessageSenderUser):
                    if sender.user_id == contact.legacy_id:
                        contact.react(update.message_id, [reaction.reaction])
                    elif sender.user_id == me:
                        contact.carbon_react(update.message_id, [reaction.reaction])


log = logging.getLogger(__name__)
