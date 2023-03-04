from datetime import datetime
from typing import TYPE_CHECKING

from slidge import LegacyContact, LegacyRoster
from slidge.plugins.whatsapp.generated import whatsapp

from . import config

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact[str]):
    # WhatsApp only allows message editing in Beta versions of their app, and support is uncertain.
    CORRECTION = False
    REACTIONS_SINGLE_EMOJI = True

    def update_presence(self, away: bool, last_seen_timestamp: int):
        last_seen = (
            datetime.fromtimestamp(last_seen_timestamp)
            if last_seen_timestamp > 0
            else None
        )
        if away:
            self.away(last_seen=last_seen)
        else:
            self.online(last_seen=last_seen)


class Roster(LegacyRoster[str, Contact]):
    session: "Session"

    async def fill(self):
        """
        Retrieve contacts from remove WhatsApp service, subscribing to their presence and adding to
        local roster.
        """
        contacts = self.session.whatsapp.GetContacts(refresh=config.ALWAYS_SYNC_ROSTER)
        for ptr in contacts:
            await self.add_contact(whatsapp.Contact(handle=ptr))

    async def add_contact(self, data: whatsapp.Contact):
        """
        Adds a WhatsApp contact to local roster, filling all required and optional information.
        """
        contact = await self.by_legacy_id(data.JID)
        contact.name = data.Name
        if data.AvatarURL != "":
            contact.avatar = data.AvatarURL
        await contact.add_to_roster()

    async def legacy_id_to_jid_username(self, legacy_id: str) -> str:
        return "+" + legacy_id[: legacy_id.find("@")]

    async def jid_username_to_legacy_id(self, jid_username: str) -> str:
        return jid_username.removeprefix("+") + "@" + whatsapp.DefaultUserServer
