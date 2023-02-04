import logging

import discord as di

from slidge import LegacyContact, LegacyParticipant, XMPPError

from .session import Session


class Mixin:
    legacy_id: int  # type: ignore
    name: str  # type: ignore
    avatar: str  # type: ignore
    session: Session  # type: ignore

    MARKS = False

    def react(self, mid: int, e: list[str]):
        raise NotImplementedError

    def send_text(self, *a, **k):
        raise NotImplementedError

    def send_file(self, *a, **k):
        raise NotImplementedError

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


class Contact(LegacyContact[Session, int], Mixin):  # type: ignore
    async def update_info(self):
        u = self.discord_user
        self.name = name = u.display_name
        if u.avatar:
            self.avatar = str(u.avatar)

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


class Participant(LegacyParticipant, Mixin):  # type: ignore
    pass
