import asyncio
import logging
from typing import Optional

from slixmpp import JID

from slidge import BaseGateway, FormField, GatewayUser
from slidge.core.command.register import RegistrationType

from .client import CredentialsValidation


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter steam credentials"
    REGISTRATION_FIELDS = [
        FormField(var="username", label="Steam username", required=True),
        FormField(var="password", label="Password", private=True, required=True),
    ]
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE

    ROSTER_GROUP = "Steam"

    COMPONENT_NAME = "Steam (slidge)"
    COMPONENT_TYPE = "steam"

    COMPONENT_AVATAR = "https://logos-download.com/wp-content/uploads/2016/05/Steam_icon_logo_logotype.png"

    def __init__(self):
        super().__init__()
        self.__pending = dict[JID, tuple[asyncio.Task, CredentialsValidation]]()

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        username = registration_form["username"]
        password = registration_form["password"]

        assert isinstance(username, str)
        assert isinstance(password, str)

        client = CredentialsValidation()
        client.user_jid = user_jid.bare

        task = self.xmpp.loop.create_task(client.login(username, password))
        self.__pending[JID(user_jid.bare)] = (task, client)

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        task, client = self.__pending.pop(user.jid)
        client.code_future.set_result(code)
        log.debug("Waiting for connected")
        await client.wait_for("login")
        log.debug("Saving token")
        client.save_token()
        log.debug("Token saved")


log = logging.getLogger(__name__)
