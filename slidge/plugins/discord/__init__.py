import logging
from typing import Optional, Union

import discord as di
from slixmpp import JID

from slidge import *

from ... import FormField
from .contact import Contact
from .session import Session


class Config:
    DISCORD_VERBOSE = False
    DISCORD_VERBOSE__DOC = (
        "Let the discord lib at the same loglevel as others loggers. "
        "By default, it's set it to WARNING because it's *really* verbose."
    )


class Gateway(BaseGateway[Session]):
    COMPONENT_NAME = "Discord (slidge)"
    COMPONENT_TYPE = "discord"
    COMPONENT_AVATAR = "https://www.usff.fr/wp-content/uploads/2018/05/Discord_logo.png"

    REGISTRATION_INSTRUCTIONS = (
        "Have a look at https://discordpy-self.readthedocs.io/en/latest/token.html"
    )
    REGISTRATION_FIELDS = [FormField("token", label="Discord token", required=True)]

    ROSTER_GROUP = "Discord"

    def __init__(self):
        super().__init__()
        if not Config.DISCORD_VERBOSE:
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


class Roster(LegacyRoster["Session", Contact, int]):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)

    async def by_discord_user(self, u: Union[di.User, di.Member]) -> Contact:
        return await self.by_legacy_id(u.id)

    async def jid_username_to_legacy_id(self, username: str):
        try:
            user_id = int(username)
        except ValueError:
            raise XMPPError(
                "bad-request",
                text=f"Not a valid discord ID: {username}",
            )
        else:
            if self.session.discord.get_user(user_id) is None:
                raise XMPPError(
                    "item-not-found",
                    text=f"No discord user was found with ID: {username}",
                )
            return user_id

    async def legacy_id_to_jid_username(self, discord_user_id: int) -> str:
        return str(discord_user_id)

    async def fill(self):
        for u in self.session.discord.users:
            if not isinstance(u, di.User):
                log.debug(f"Skipping %s", u)
                continue
            if not u.is_friend():
                log.debug(f"%s is not a friend", u)
                continue
            c = await self.by_legacy_id(u.id)
            await c.add_to_roster()
            # TODO: contribute to discord.py-self so that the presence information
            #       of relationships is parsed. logs show:
            #       'PRESENCE_UPDATE referencing an unknown guild ID: %s. Discarding.'
            #       https://github.com/dolfies/discord.py-self/blob/master/discord/state.py#L1044
            c.online()


log = logging.getLogger(__name__)
