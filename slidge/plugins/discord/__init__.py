import logging
from typing import Optional, Union

import discord as di
from slixmpp import JID

from slidge import *

from ... import FormField
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


class Contact(LegacyContact[Session, int]):
    MARKS = False

    @property
    def discord_user(self) -> di.User:
        logging.debug("Searching for user: %s", self.legacy_id)
        if (u := self.session.discord.get_user(self.legacy_id)) is None:
            raise XMPPError(
                "item-not-found", text=f"Cannot find the discord user {self.legacy_id}"
            )
        return u

    @property
    def direct_channel_id(self):
        assert self.discord_user.dm_channel is not None
        return self.discord_user.dm_channel.id

    async def update_reactions(self, m: di.Message):
        legacy_reactions = []
        user = self.discord_user
        for r in m.reactions:
            if r.is_custom_emoji():
                continue
            assert isinstance(r.emoji, str)
            async for u in r.users():
                if u == user:
                    legacy_reactions.append(r.emoji)
        self.react(m.id, legacy_reactions)

    async def update_info(self):
        u = self.discord_user
        self.name = name = u.display_name
        if u.avatar:
            self.avatar = str(u.avatar)

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

    async def send_message(self, message: di.Message):
        reply_to = message.reference.message_id if message.reference else None

        text = message.content
        attachments = message.attachments
        msg_id = message.id

        if not attachments:
            return self.send_text(text, legacy_msg_id=msg_id, reply_to_msg_id=reply_to)

        last_attachment_i = len(attachments := message.attachments) - 1
        for i, attachment in enumerate(attachments):
            last = i == last_attachment_i
            await self.send_file(
                file_url=attachment.url,
                file_name=attachment.filename,
                content_type=attachment.content_type,
                reply_to_msg_id=reply_to if last else None,
                legacy_msg_id=msg_id if last else None,
                caption=text if last else None,
            )


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
