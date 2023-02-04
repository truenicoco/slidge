from typing import Any, Union

import discord as di

from slidge import LegacyContact, LegacyRoster, XMPPError

from .session import Session
from .util import Mixin


class Contact(LegacyContact[Session, int], Mixin):  # type: ignore
    @property
    def discord_user(self) -> di.User:  # type:ignore
        self.session.log.debug("Searching for user: %s", self.legacy_id)
        if (u := self.session.discord.get_user(self.legacy_id)) is None:
            raise XMPPError(
                "item-not-found", text=f"Cannot find the discord user {self.legacy_id}"
            )
        return u

    @property
    def direct_channel_id(self):
        assert self.discord_user.dm_channel is not None
        return self.discord_user.dm_channel.id

    async def get_reply_to_kwargs(self, message: di.Message):
        reference = message.reference.message_id if message.reference else None
        reply_kwargs = dict[str, Any](reply_to=reference)
        if not reference:
            return reply_kwargs

        reply_to_message = await message.channel.fetch_message(reference)
        reply_kwargs["reply_to_fallback_text"] = reply_to_message.content
        reply_kwargs["reply_self"] = reply_to_message.author == message.author

        return reply_kwargs

    async def update_info(self):
        u = self.discord_user
        self.name = name = u.display_name
        if u.avatar:
            self.avatar = str(u.avatar)

        # massive rate limiting if trying to fetch profiles of non friends
        if not u.is_friend():
            return

        try:
            profile = await u.profile()
        except di.Forbidden:
            self.session.log.debug("Forbidden to fetch the profile of %s", u)
        except di.HTTPException as e:
            self.session.log.debug(
                "HTTP exception %s when fetch the profile of %s", e, u
            )
        else:
            self.set_vcard(full_name=name, note=profile.bio)

        # TODO: use the relationship here
        # relationship = u.relationship


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
        for u in self.session.discord.users:
            if not isinstance(u, di.User):
                self.session.log.debug(f"Skipping %s", u)
                continue
            if not u.is_friend():
                self.session.log.debug(f"%s is not a friend", u)
                continue
            c = await self.by_legacy_id(u.id)
            await c.add_to_roster()
            # TODO: parse presence
            c.online()
