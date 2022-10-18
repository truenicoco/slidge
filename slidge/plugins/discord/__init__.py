import functools
import logging
from argparse import ArgumentParser

import discord as di
from slixmpp.exceptions import XMPPError

from slidge import *

from .session import Session


class Gateway(BaseGateway[Session]):
    COMPONENT_NAME = "Discord (slidge)"
    COMPONENT_TYPE = "discord"
    REGISTRATION_INSTRUCTIONS = (
        "Have a look at https://discordpy-self.readthedocs.io/en/latest/token.html"
    )
    REGISTRATION_FIELDS = [FormField("token", required=True)]

    ROSTER_GROUP = "Discord"

    def config(self, argv: list[str]):
        parser = ArgumentParser()
        parser.add_argument("--discord-verbose", action="store_true")
        args = parser.parse_args(argv)
        if not args.discord_verbose:
            log.debug("Disabling discord info logs")
            logging.getLogger("discord.gateway").setLevel(logging.WARNING)
            logging.getLogger("discord.client").setLevel(logging.WARNING)


class Contact(LegacyContact[Session]):
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


class Roster(LegacyRoster[Contact, "Session"]):
    def by_discord_user(self, u: di.User):
        return self.by_legacy_id(u.id)

    @staticmethod
    def jid_username_to_legacy_id(discord_id: str):
        try:
            return int(discord_id)
        except ValueError:
            raise XMPPError(
                "not-found", text=f"Not a valid discord user ID: {discord_id}"
            )


log = logging.getLogger(__name__)
