import functools
from typing import TYPE_CHECKING

import aiotdlib
from aiotdlib import api as tgapi

from .contact import (
    on_contact_chat_action,
    on_contact_edit_msg,
    on_contact_read,
    on_contact_status,
    on_msg_interaction_info,
    on_telegram_message,
    on_user_read_from_other_device,
    on_user_update,
)
from .session import on_message_success

if TYPE_CHECKING:
    from .session import Session


class TelegramClient(aiotdlib.Client):
    def __init__(self, session: "Session", **kw):
        super().__init__(parse_mode=aiotdlib.ClientParseMode.MARKDOWN, **kw)
        self.session = session

        async def input_(prompt):
            self.session.send_gateway_status(f"Action required: {prompt}")
            return await session.input(prompt)

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
            (on_msg_interaction_info, tgapi.API.Types.UPDATE_MESSAGE_INTERACTION_INFO),
        ]:
            self.add_event_handler(h, t)
