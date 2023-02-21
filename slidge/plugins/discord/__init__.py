import logging
from typing import Optional

import discord as di
from slixmpp import JID

from slidge import BaseGateway, FormField

from . import config, group
from .contact import Contact
from .session import Session


class Gateway(BaseGateway):
    COMPONENT_NAME = "Discord (slidge)"
    COMPONENT_TYPE = "discord"
    COMPONENT_AVATAR = "https://www.usff.fr/wp-content/uploads/2018/05/Discord_logo.png"

    REGISTRATION_INSTRUCTIONS = (
        "Have a look at https://discordpy-self.readthedocs.io/en/latest/token.html"
    )
    REGISTRATION_FIELDS = [FormField("token", label="Discord token", required=True)]

    ROSTER_GROUP = "Discord"

    GROUPS = True

    def __init__(self):
        super().__init__()
        if not config.DISCORD_VERBOSE:
            log.debug("Disabling discord info logs")
            logging.getLogger("discord.gateway").setLevel(logging.WARNING)
            logging.getLogger("discord.client").setLevel(logging.WARNING)

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        token = registration_form.get("token")
        assert isinstance(token, str)
        try:
            await di.Client().login(token)
        except di.LoginFailure as e:
            raise ValueError(str(e))


log = logging.getLogger(__name__)
