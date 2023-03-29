from typing import TYPE_CHECKING, Union

import discord as di

from slidge import LegacyContact, LegacyRoster, XMPPError

from .util import MessageMixin, StatusMixin

if TYPE_CHECKING:
    from .session import Session


class Contact(StatusMixin, MessageMixin, LegacyContact[int]):  # type: ignore
    session: "Session"

    @property
    def discord_user(self) -> di.User:  # type: ignore
        self.session.log.debug("Searching for user: %s", self.legacy_id)
        user = self.session.discord.get_user(self.legacy_id)
        # self.session.discord.get_guild().get_member()
        if user is None:
            raise XMPPError(
                "item-not-found", text=f"Cannot find the discord user {self.legacy_id}"
            )
        return user

    @property
    def direct_channel_id(self):
        assert self.discord_user.dm_channel is not None
        return self.discord_user.dm_channel.id

    async def update_info(self):
        u = self.discord_user
        if u.bot or u.system:
            self.DISCO_CATEGORY = "bot"
        self.name = u.display_name
        if a := u.avatar:
            await self.set_avatar(a.url, a.key)

        # massive rate limiting if trying to fetch profiles of non friends
        if u.is_friend():
            await self.fetch_vcard()

        # TODO: use the relationship here
        # relationship = u.relationship

    async def fetch_vcard(self):
        try:
            profile = await self.discord_user.profile(fetch_note=False)
        except di.Forbidden:
            self.session.log.debug("Forbidden to fetch the profile of %s", self)
        except di.HTTPException as e:
            self.session.log.debug(
                "HTTP exception %s when fetch the profile of %s", e, self
            )
        else:
            self.set_vcard(full_name=self.name, note=profile.bio)


class Roster(LegacyRoster[int, Contact]):
    session: "Session"

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
                self.session.log.debug(
                    "I could not find the JID local part %s", username
                )
                raise XMPPError(
                    "item-not-found",
                    text=f"No discord user was found with ID: {username}",
                )
            return user_id

    async def legacy_id_to_jid_username(self, discord_user_id: int) -> str:
        return str(discord_user_id)

    async def fill(self):
        for relationship in self.session.discord.friends:
            u = relationship.user
            self.session.log.debug("Friend: %r", u)
            if not isinstance(u, di.User):
                self.session.log.debug("Skipping %s", u)
                continue
            c = await self.by_legacy_id(u.id)
            await c.add_to_roster()
            c.update_status(relationship.status, relationship.activity)
