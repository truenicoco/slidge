import asyncio
import functools
from datetime import datetime
from typing import TYPE_CHECKING, Union

import aiotdlib
from aiotdlib import api as tgapi

from . import config
from .util import get_best_file

if TYPE_CHECKING:
    from .contact import Contact
    from .group import MUC, Participant
    from .session import Session


def get_base_kwargs(user_reg_form: dict):
    return dict(
        phone_number=user_reg_form["phone"],
        api_id=user_reg_form.get("api_id") or config.API_ID,
        api_hash=user_reg_form.get("api_hash") or config.API_HASH,
        database_encryption_key=config.TDLIB_KEY,
        files_directory=config.TDLIB_PATH,
    )


class CredentialsValidation(aiotdlib.Client):
    def __init__(self, registration_form: dict):
        super().__init__(**get_base_kwargs(registration_form))
        self.code_future: asyncio.Future[
            str
        ] = asyncio.get_running_loop().create_future()
        self._auth_get_code = self._get_code
        self._auth_get_password = self._get_code

    async def _get_code(self):
        return await self.code_future


class TelegramClient(aiotdlib.Client):
    def __init__(self, session: "Session"):
        super().__init__(
            parse_mode=aiotdlib.ClientParseMode.MARKDOWN,
            **get_base_kwargs(session.user.registration_form),
        )
        self.session = session
        self.contacts = session.contacts
        self.bookmarks = session.bookmarks
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
            return await self.handle_group_message(msg)

        session = self.session
        if msg.is_outgoing:
            # This means slidge is responsible for this message, so no carbon is needed;
            # but maybe this does not handle all possible cases gracefully?
            if msg.sending_state is not None or msg.id in session.sent:
                return
            content = msg.content
            contact = await session.contacts.by_legacy_id(msg.chat_id)
            if isinstance(content, tgapi.MessageText):
                contact.send_text(
                    content.text.text,
                    legacy_msg_id=msg.id,
                    when=datetime.fromtimestamp(msg.date),
                    carbon=True,
                )
            elif best_file := get_best_file(content):
                file = await self.api.download_file(
                    file_id=best_file.id,
                    synchronous=True,
                    priority=1,
                    offset=0,
                    limit=0,
                )
                has_caption = (caption := content.caption) and (text := caption.text)
                await contact.send_file(
                    filename=file.local.path,
                    legacy_msg_id=None if has_caption else msg.id,
                    carbon=True,
                )
                if has_caption:
                    contact.send_text(text, legacy_msg_id=msg.id, carbon=True)
            return

        sender = msg.sender_id
        if not isinstance(sender, tgapi.MessageSenderUser):
            # Does this happen?
            self.log.warning("Ignoring chat sender in direct message: %s", msg)
            return

        await (await session.contacts.by_legacy_id(sender.user_id)).send_tg_message(msg)

    async def handle_group_message(self, msg: tgapi.Message):
        self.log.debug("MUC message: %s", msg)
        if msg.is_outgoing:
            if msg.sending_state is not None or msg.id in self.session.sent:
                return

        muc = await self.bookmarks.by_legacy_id(msg.chat_id)
        sender = msg.sender_id
        if isinstance(sender, tgapi.MessageSenderUser):
            participant = await muc.participant_by_tg_user(
                await self.api.get_user(sender.user_id)
            )
        else:
            participant = await muc.participant_system()
        await participant.send_tg_message(msg)

    async def handle_UserStatus(self, update: tgapi.UpdateUserStatus):
        if update.user_id == await self.get_my_id():
            return
        contact = await self.contacts.by_legacy_id(update.user_id)
        if not contact.added_to_roster:
            self.log.debug("Ignoring presence of contact not in the roster")
            return
        contact.update_status(update.status)

    async def handle_ChatReadOutbox(self, update: tgapi.UpdateChatReadOutbox):
        if await self.is_private_chat(update.chat_id):
            contact = await self.contacts.by_legacy_id(update.chat_id)
            contact.displayed(update.last_read_outbox_message_id)
        else:
            muc = await self.bookmarks.by_legacy_id(update.chat_id)
            async for p in muc.get_participants():
                p.displayed(update.last_read_outbox_message_id)

    async def handle_ChatAction(self, action: tgapi.UpdateChatAction):
        sender = action.sender_id
        if not isinstance(sender, tgapi.MessageSenderUser):
            self.log.debug("Ignoring action: %s", action)
            return

        chat_id = action.chat_id
        user_id = sender.user_id
        if chat_id == user_id:
            composer: Union[
                "Contact", "Participant"
            ] = await self.contacts.by_legacy_id(chat_id)
        else:
            muc: MUC = await self.bookmarks.by_legacy_id(chat_id)
            composer = await muc.participant_by_tg_user(
                await self.api.get_user(user_id)
            )

        composer.composing()

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
            contact = await session.contacts.by_legacy_id(action.chat_id)
            contact.displayed(msg_id, carbon=True)

    async def handle_MessageContent(self, action: tgapi.UpdateMessageContent):
        new = action.new_content
        if isinstance(new, tgapi.MessagePhoto):
            # Happens when the user send a picture, looks safe to ignore
            self.log.debug("Ignoring message photo update: %s", new)
            return
        if not isinstance(new, tgapi.MessageText):
            self.log.warning("Ignoring message update: %s", new)
            return
        session = self.session
        corrected_msg_id = action.message_id
        chat_id = action.chat_id
        try:
            fut = session.user_correction_futures.pop(action.message_id)
        except KeyError:
            if await self.is_private_chat(chat_id):
                contact = await session.contacts.by_legacy_id(chat_id)
                if action.message_id in self.session.sent:
                    contact.correct(corrected_msg_id, new.text.text, carbon=True)
                else:
                    contact.correct(corrected_msg_id, new.text.text)
            else:
                if action.message_id not in self.session.muc_sent_msg_ids:
                    muc = await session.bookmarks.by_legacy_id(chat_id)
                    msg = await self.api.get_message(chat_id, corrected_msg_id)
                    participant = await muc.participant_by_tg_user(
                        await self.api.get_user(msg.sender_id.user_id)
                    )
                    participant.correct(action.message_id, new.text.text)
        else:
            self.log.debug("User correction confirmation received")
            fut.set_result(None)

    async def handle_User(self, action: tgapi.UpdateUser):
        u = action.user
        if u.id == await self.get_my_id():
            return
        contact = await self.session.contacts.by_legacy_id(u.id)
        await contact.update_info_from_user(u)
        if u.is_contact:
            await contact.add_to_roster()

    async def handle_MessageInteractionInfo(
        self, update: tgapi.UpdateMessageInteractionInfo
    ):
        if not await self.is_private_chat(update.chat_id):
            return

        contact = await self.session.contacts.by_legacy_id(update.chat_id)
        me = await self.get_my_id()
        if update.interaction_info is None:
            contact.react(update.message_id, [])
            contact.react(update.message_id, [], carbon=True)
        else:
            user_reactions = list[str]()
            contact_reactions = list[str]()
            # these sanity checks might not be necessary, but in doubt…
            for reaction in update.interaction_info.reactions:
                if reaction.total_count == 1:
                    if len(reaction.recent_sender_ids) != 1:
                        self.log.warning(
                            "Weird reactions (wrong count): %s",
                            update.interaction_info.reactions,
                        )
                        continue
                    sender = reaction.recent_sender_ids[0]
                    if isinstance(sender, tgapi.MessageSenderUser):
                        if sender.user_id == me:
                            user_reactions.append(reaction.reaction)
                        elif sender.user_id == contact.legacy_id:
                            contact_reactions.append(reaction.reaction)
                    else:
                        self.log.warning(
                            "Weird reactions (neither me nor them): %s",
                            update.interaction_info.reactions,
                        )
                elif reaction.total_count == 2:
                    user_reactions.append(reaction.reaction)
                    contact_reactions.append(reaction.reaction)
                else:
                    self.log.warning(
                        "Weird reactions (empty): %s", update.interaction_info.reactions
                    )

            contact.react(update.message_id, contact_reactions)
            contact.react(update.message_id, user_reactions, carbon=True)

    async def handle_DeleteMessages(self, update: tgapi.UpdateDeleteMessages):
        if not update.is_permanent:  # tdlib send 'delete from cache' updates apparently
            self.log.debug("Ignoring non permanent delete")
            return
        for legacy_msg_id in update.message_ids:
            try:
                future = self.session.delete_futures.pop(legacy_msg_id)
            except KeyError:
                if await self.is_private_chat(update.chat_id):
                    contact = await self.session.contacts.by_legacy_id(update.chat_id)
                    if legacy_msg_id in self.session.sent:
                        contact.retract(legacy_msg_id, carbon=True)
                    else:
                        contact.retract(legacy_msg_id)
                else:
                    return
                    # FIXME: does not work because we need to fetch the participant,
                    #        the DeleteMessage payload has not author info,
                    #        and we cannot get_message() anymore
                    # We should probably use MUC moderation tools here
                    # muc = await self.session.bookmarks.by_legacy_id(update.chat_id)
                    # msg = await self.api.get_message(update.chat_id, legacy_msg_id)
                    # participant = await muc.participant_by_tg_user_id(
                    #     msg.sender_id.user_id
                    # )
                    # participant.retract(legacy_msg_id)
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
        return isinstance(chat.type_, tgapi.ChatTypePrivate)
