import asyncio
import functools
from datetime import datetime
from typing import TYPE_CHECKING

import aiotdlib
from aiotdlib import api as tgapi

if TYPE_CHECKING:
    from .session import Session


class TelegramClient(aiotdlib.Client):
    def __init__(self, session: "Session", **kw):
        super().__init__(parse_mode=aiotdlib.ClientParseMode.MARKDOWN, **kw)
        self.session = session
        self.contacts = session.contacts
        self.log = self.session.log

        async def input_(prompt):
            self.session.send_gateway_status(f"Action required: {prompt}")
            return await session.input(prompt)

        self.input = input_
        self._auth_get_code = functools.partial(input_, "Enter code")
        self._auth_get_password = functools.partial(input_, "Enter 2FA password:")
        self._auth_get_first_name = functools.partial(input_, "Enter first name:")
        self._auth_get_last_name = functools.partial(input_, "Enter last name:")

        self.add_event_handler(self.dispatch_update, tgapi.API.Types.ANY)

    async def dispatch_update(self, _self, update: tgapi.Update):
        try:
            handler = getattr(self, "handle_" + update.ID[6:])
        except AttributeError:
            self.session.log.debug("No handler for %s, ignoring", update.ID)
        except IndexError:
            self.session.log.debug("Ignoring weird event: %s", update.ID)
        else:
            await handler(update)

    async def handle_NewMessage(self, update: tgapi.UpdateNewMessage):
        if (msg := update.message).is_channel_post:
            self.log.debug("Ignoring channel post")
            return

        if not await self.is_private_chat(msg.chat_id):
            self.log.debug("Ignoring group message")
            return

        session = self.session
        if msg.is_outgoing:
            # This means slidge is responsible for this message, so no carbon is needed;
            # but maybe this does not handle all possible cases gracefully?
            if msg.sending_state is not None or msg.id in session.sent:
                return
            content = msg.content
            if isinstance(content, tgapi.MessageText):
                session.contacts.by_legacy_id(msg.chat_id).carbon(
                    content.text.text, msg.id, datetime.fromtimestamp(msg.date)
                )
            # TODO: implement carbons for other contents
            return

        sender = msg.sender_id
        if not isinstance(sender, tgapi.MessageSenderUser):
            self.log.debug("Ignoring non-user sender")  # Does this happen?
            return

        await session.contacts.by_legacy_id(sender.user_id).send_tg_message(msg)

    async def handle_UserStatus(self, update: tgapi.UpdateUserStatus):
        if update.user_id == await self.get_my_id():
            return
        contact = self.contacts.by_legacy_id(update.user_id)
        if not contact.added_to_roster:
            self.log.debug("Ignoring presence of contact not in the roster")
            return
        await contact.send_tg_status(update.status)

    async def handle_ChatReadOutbox(self, update: tgapi.UpdateChatReadOutbox):
        if not await self.is_private_chat(update.chat_id):
            return
        self.contacts.by_legacy_id(update.chat_id).displayed(
            update.last_read_outbox_message_id
        )

    async def handle_ChatAction(self, action: tgapi.UpdateChatAction):
        if not await self.is_private_chat(action.chat_id):
            return

        sender = action.sender_id
        if not isinstance(sender, tgapi.MessageSenderUser):
            self.log.debug("Ignoring action: %s", action)
            return

        if (chat_id := action.chat_id) != sender.user_id:
            self.log.debug("Ignoring group (?) action: %s", action)
            return

        self.contacts.by_legacy_id(chat_id).composing()

    async def handle_ChatReadInbox(self, action: tgapi.UpdateChatReadInbox):
        if not await self.is_private_chat(action.chat_id):
            return

        session = self.session
        msg_id = action.last_read_inbox_message_id
        self.log.debug(
            "Self read mark for %s and we sent %s", msg_id, session.sent_read_marks
        )
        try:
            session.sent_read_marks.remove(msg_id)
        except KeyError:
            # slidge didn't send this read mark, so it comes from the official tg client
            contact = session.contacts.by_legacy_id(action.chat_id)
            contact.carbon_read(msg_id)

    async def handle_MessageContent(self, action: tgapi.UpdateMessageContent):
        if not await self.is_private_chat(action.chat_id):
            return

        new = action.new_content
        if not isinstance(new, tgapi.MessageText):
            raise NotImplementedError(new)
        session = self.session
        try:
            fut = session.user_correction_futures.pop(action.message_id)
        except KeyError:
            contact = session.contacts.by_legacy_id(action.chat_id)
            if action.message_id in self.session.sent:
                contact.carbon_correct(action.message_id, new.text.text)
            else:
                contact.correct(action.message_id, new.text.text)
        else:
            self.log.debug("User correction confirmation received")
            fut.set_result(None)

    async def handle_User(self, action: tgapi.UpdateUser):
        u = action.user
        if u.id == await self.get_my_id():
            return
        await self.request(
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
        contact = self.session.contacts.by_legacy_id(u.id)
        contact.name = u.first_name
        await contact.add_to_roster()

    async def handle_MessageInteractionInfo(
        self, update: tgapi.UpdateMessageInteractionInfo
    ):
        if not await self.is_private_chat(update.chat_id):
            return

        contact = self.session.contacts.by_legacy_id(update.chat_id)
        me = await self.get_my_id()
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

    async def handle_DeleteMessages(self, update: tgapi.UpdateDeleteMessages):
        if not await self.is_private_chat(update.chat_id):
            return

        if not update.is_permanent:  # tdlib send 'delete from cache' updates apparently
            self.log.debug("Ignoring non permanent delete")
            return
        for legacy_msg_id in update.message_ids:
            try:
                future = self.session.delete_futures.pop(legacy_msg_id)
            except KeyError:
                # FIXME: where do we filter out group chat messages here ?!
                contact = self.session.contacts.by_legacy_id(update.chat_id)
                if legacy_msg_id in self.session.sent:
                    contact.carbon_retract(legacy_msg_id)
                else:
                    contact.retract(legacy_msg_id)
            else:
                future.set_result(update)

    async def handle_MessageSendSucceeded(
        self, update: tgapi.UpdateMessageSendSucceeded
    ):
        self.session.sent_read_marks.add(update.message.id)
        for _ in range(10):
            try:
                future = self.session.ack_futures.pop(update.message.id)
            except KeyError:
                await asyncio.sleep(0.5)
            else:
                future.set_result(update.message.id)
                return
        self.log.warning("Ignoring Send success for %s", update.message.id)

    async def is_private_chat(self, chat_id: int):
        chat = await self.get_chat(chat_id)
        return isinstance(chat, tgapi.ChatTypePrivate)
