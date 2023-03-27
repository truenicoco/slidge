from typing import TYPE_CHECKING, Optional

import steam
from steam.types.id import ID32

from slidge import LegacyContact, LegacyRoster, XMPPError

from .util import EMOJIS

if TYPE_CHECKING:
    from .session import Session


class Roster(LegacyRoster[ID32, "Contact"]):
    session: "Session"

    async def by_steam_user(self, user: steam.User):
        return await self.by_legacy_id(user.id)

    async def jid_username_to_legacy_id(self, local: str):
        return ID32(int(local))

    async def fill(self):
        for user in await self.session.steam.user.friends():
            c = await self.by_steam_user(user)
            await c.add_to_roster()


class Contact(LegacyContact[ID32]):
    MARKS = False
    CORRECTION = False
    RETRACTION = False

    session: "Session"

    async def get_user(self):
        u = self.session.steam.get_user(self.legacy_id)
        if u is None:
            raise XMPPError("item-not-found")
        return u

    async def update_info(self, user: Optional[steam.User] = None):
        if user is None:
            user = await self.get_user()
        self.name = user.name
        self.avatar = user.avatar.url
        await self.update_state(user)

    async def update_state(self, user: Optional[steam.User] = None):
        if user is None:
            user = await self.get_user()
        self.log.debug("Rich presence: %s", user.rich_presence)
        match user.state:
            case steam.PersonaState.Online:
                self.online(last_seen=user.last_seen_online)
            case steam.PersonaState.Offline:
                self.offline(last_seen=user.last_seen_online)
            case steam.PersonaState.Busy:
                self.busy(last_seen=user.last_seen_online)
            case steam.PersonaState.Away:
                self.away(last_seen=user.last_seen_online)
            case steam.PersonaState.Snooze:
                self.extended_away(last_seen=user.last_seen_online)
            case steam.PersonaState.LookingToPlay:
                self.online(status="Looking to play", last_seen=user.last_seen_online)
            case steam.PersonaState.LookingToTrade:
                self.online(status="Looking to trade", last_seen=user.last_seen_online)

    async def available_emojis(self, legacy_msg_id=None):
        return set(EMOJIS.values())
