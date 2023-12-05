from slixmpp.exceptions import XMPPError

from slidge import LegacyContact, LegacyRoster

from .session import Session


class Roster(LegacyRoster[int, "Contact"]):
    async def fill(self):
        for i in 111, 222:
            contact = await self.by_legacy_id(i)
            await contact.add_to_roster()

    async def jid_username_to_legacy_id(self, jid_username: str) -> int:
        try:
            return int(jid_username)
        except ValueError:
            raise XMPPError(
                "bad-request", "This is not a valid username for this fake network"
            )


class Contact(LegacyContact[int]):
    session: "Session"

    async def update_info(self):
        profile = await self.session.legacy_client.get_profile(self.legacy_id)
        self.name = profile.nickname
        self.set_vcard(full_name=profile.full_name)
        await self.set_avatar(profile.avatar, profile.avatar_unique_id)
        self.is_friend = True
        self.online()