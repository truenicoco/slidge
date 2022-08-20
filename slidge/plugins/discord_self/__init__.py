import functools
from typing import Optional

import discord as di

from slidge import *

from .session import Session


class Gateway(BaseGateway[Session]):
    COMPONENT_NAME = "Discord (slidge)"
    REGISTRATION_INSTRUCTIONS = (
        "Have a look at https://discordpy-self.readthedocs.io/en/latest/token.html"
    )
    REGISTRATION_FIELDS = [FormField("token", required=True)]

    ROSTER_GROUP = "Discord"


class Contact(LegacyContact[Session]):
    MARKS = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._discord_id: Optional[int] = None

    @property
    def discord_id(self):
        if self._discord_id is None:
            for u in self.session.discord.users:
                if not isinstance(u, di.User):
                    continue
                if str(u) == self.legacy_id:
                    self._discord_id = u.id
                    break
        return self._discord_id

    @discord_id.setter
    def discord_id(self, i: int):
        self._discord_id = i

    @functools.cached_property
    def discord_user(self) -> di.User:
        return self.session.discord.get_user(self._discord_id)

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


class Roster(LegacyRoster[Contact, "Session"]):
    def by_discord_user(self, u: di.User):
        return self.by_legacy_id(str(u))
