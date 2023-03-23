import asyncio
import functools
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Union

import aiotdlib
from aiotdlib import api as tgapi
from aiotdlib.api import BaseObject
from aiotdlib.client import RequestResult

from slidge import XMPPError

from . import config
from .util import get_best_file, get_file_name

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

    async def request(
        self,
        query: BaseObject,
        *,
        request_id: Optional[str] = None,
        request_timeout=60,
    ) -> Optional[RequestResult]:
        if not self.session.logged:
            # during login, aiotdlib relies on its own exceptions for flow control
            return await super().request(
                query, request_id=request_id, request_timeout=request_timeout
            )
        # after login, we can safely (hopefully) map all these errors to XMPPErrors
        try:
            return await super().request(
                query, request_id=request_id, request_timeout=request_timeout
            )
        except asyncio.TimeoutError:
            raise XMPPError("remote-server-timeout", "Telegram did not respond in time")
        except tgapi.BadRequest as e:
            raise XMPPError("bad-request", e.message)
        except tgapi.Unauthorized as e:
            raise XMPPError("not-authorized", e.message)
        except tgapi.NotFound as e:
            raise XMPPError("item-not-found", e.message)

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
        msg = update.message
        if await self.is_private_chat(msg.chat_id):
            await self.handle_direct_message(msg)
        else:
            await self.handle_group_message(msg)

    async def handle_direct_message(self, msg: tgapi.Message):
        carbon = msg.is_outgoing

        sender = msg.sender_id
        if not isinstance(sender, tgapi.MessageSenderUser):
            # Does this happen?
            self.log.warning("Ignoring chat sender in direct message: %s", msg)
            return

        session = self.session
        await (await session.contacts.by_legacy_id(msg.chat_id)).send_tg_message(
            msg, carbon=carbon
        )

    async def handle_group_message(self, msg: tgapi.Message):
        self.log.debug("MUC message: %s", msg)
        if msg.is_outgoing:
            if msg.sending_state is not None or msg.id in self.session.sent:
                return

        muc = await self.bookmarks.by_legacy_id(msg.chat_id)
        participant = await muc.participant_by_sender_id(msg.sender_id)
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
            # telegram does not have individual read markers for groups,
            # this means "at least someone has read"
            # mapping to the room itself is not great, but is what seems more natural
            muc = await self.bookmarks.by_legacy_id(update.chat_id)
            p = muc.get_system_participant()
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
            composer = await muc.participant_by_tg_user(await self.get_user(user_id))

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
            self.log.debug("Ignoring message photo update")
            return
        if not isinstance(new, tgapi.MessageText):
            self.log.warning("Ignoring message update: %s", new)
            return
        if new.web_page:
            self.log.debug("Ignoring update with web_page")
            return

        session = self.session
        corrected_msg_id = action.message_id
        chat_id = action.chat_id

        fut = session.user_correction_futures.pop(action.message_id, None)
        if fut is not None:
            self.log.debug("User correction confirmation received")
            fut.set_result(None)
            return

        msg = await self.api.get_message(chat_id, corrected_msg_id)

        if await self.is_private_chat(chat_id):
            contact = await session.contacts.by_legacy_id(chat_id)
            if not isinstance(msg.sender_id, tgapi.MessageSenderUser):
                self.log.warning("Weird message update: %s", action)
                return
            if msg.sender_id.user_id == await self.get_my_id():
                contact.correct(corrected_msg_id, new.text.text, carbon=True)
            else:
                contact.correct(corrected_msg_id, new.text.text)
            return

        muc = await session.bookmarks.by_legacy_id(chat_id)
        if action.message_id in self.session.muc_sent_msg_ids:
            participant = await muc.get_user_participant()
        else:
            msg = await self.api.get_message(chat_id, corrected_msg_id)
            participant = await muc.participant_by_tg_user(
                await self.get_user(msg.sender_id.user_id)
            )
        participant.correct(action.message_id, new.text.text)

    async def handle_User(self, action: tgapi.UpdateUser):
        u = action.user
        if u.id == await self.get_my_id():
            return
        contact = await self.session.contacts.by_legacy_id(u.id)
        await contact.update_info(u)

    async def handle_MessageInteractionInfo(
        self, update: tgapi.UpdateMessageInteractionInfo
    ):
        if not await self.is_private_chat(update.chat_id):
            return await self.react_group(update)

        contact = await self.session.contacts.by_legacy_id(update.chat_id)
        me = await self.get_my_id()
        if update.interaction_info is None:
            contact.react(update.message_id, [])
            contact.react(update.message_id, [], carbon=True)
            return

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

    async def react_group(self, update: tgapi.UpdateMessageInteractionInfo):
        muc = await self.bookmarks.by_legacy_id(update.chat_id)
        if update.interaction_info is None:
            while True:
                try:
                    reacter, _ = muc.reactions[update.message_id].pop()
                except KeyError:
                    return
                reacter.react(update.message_id)

        old_reacters = muc.reactions[update.message_id]
        new_reacters = set()
        for reaction in update.interaction_info.reactions:
            emoji = reaction.reaction

            for sender_id in reaction.recent_sender_ids:
                if isinstance(sender_id, tgapi.MessageSenderUser):
                    reacter = await muc.get_participant_by_legacy_id(sender_id.user_id)
                else:
                    reacter = muc.get_system_participant()
                new_reacters.add((reacter, emoji))

        self.log.debug("Old reacters: %s", old_reacters)
        self.log.debug("New reacters: %s", new_reacters)

        old_all_reacters = {x[0] for x in old_reacters}
        new_all_reacters = {x[0] for x in new_reacters}
        for unreacter in old_all_reacters - new_all_reacters:
            unreacter.react(update.message_id)
        for reacter, emoji in new_reacters - old_reacters:
            reacter.react(update.message_id, emoji)

        muc.reactions[update.message_id] = new_reacters

    async def handle_DeleteMessages(self, update: tgapi.UpdateDeleteMessages):
        if not update.is_permanent:  # tdlib send 'delete from cache' updates apparently
            self.log.debug("Ignoring non permanent delete")
            return

        direct = await self.is_private_chat(update.chat_id)

        if direct:
            contact = await self.session.contacts.by_legacy_id(update.chat_id)
        else:
            muc: "MUC" = await self.session.bookmarks.by_legacy_id(update.chat_id)
            p = muc.get_system_participant()

        for legacy_msg_id in update.message_ids:
            future = self.session.delete_futures.pop(legacy_msg_id, None)
            if future is not None:
                future.set_result(update)
                continue

            if direct:
                if legacy_msg_id in self.session.sent:
                    contact.retract(legacy_msg_id, carbon=True)
                else:
                    contact.retract(legacy_msg_id)
            else:
                p.moderate(legacy_msg_id)

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
