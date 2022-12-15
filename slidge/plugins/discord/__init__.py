import functools
import logging
from typing import Optional

import discord as di
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *

from ...util import BiDict
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
        try:
            await di.Client().login(registration_form.get("token"))
        except di.LoginFailure as e:
            raise ValueError(str(e))


class Contact(LegacyContact[Session, "str"]):
    MARKS = False

    @functools.cached_property
    def discord_user(self) -> di.User:
        logging.debug("Searching for user: %s", self.legacy_id)
        if (u := self.session.discord.get_user(self.legacy_id)) is None:
            raise XMPPError(
                "not-found", text=f"Cannot find the discord user {self.legacy_id}"
            )
        return u

    @functools.cached_property
    def direct_channel_id(self):
        return self.discord_user.dm_channel.id

    async def update_reactions(self, m: di.Message):
        legacy_reactions = []
        user = self.discord_user
        for r in m.reactions:
            async for u in r.users():
                if u == user:
                    legacy_reactions.append(r.emoji)
        self.react(m.id, legacy_reactions)

    async def update_info(self):
        u = self.discord_user
        self.name = name = u.display_name
        self.avatar = str(u.avatar_url)

        try:
            profile = await u.profile()
        except di.Forbidden:
            log.debug("Forbidden to fetch the profile of %s", u)
        except di.HTTPException as e:
            log.debug("HTTP exception %s when fetch the profile of %s", e, u)
        else:
            self.set_vcard(full_name=name, note=profile.bio)

        # TODO: use the relationship here
        # relationship = u.relationship


class Roster(LegacyRoster["Session", Contact, int]):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)

    async def by_discord_user(self, u: di.User):
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


log = logging.getLogger(__name__)
