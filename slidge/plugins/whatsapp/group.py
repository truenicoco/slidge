from typing import TYPE_CHECKING

from slidge.core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from slidge.plugins.whatsapp.generated import whatsapp

from ... import XMPPError

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Participant(LegacyParticipant):
    contact: "Contact"
    muc: "MUC"


class MUC(LegacyMUC[str, str, Participant]):
    session: "Session"

    REACTIONS_SINGLE_EMOJI = True
    type = MucType.GROUP

    async def get_whatsapp_group_info(self) -> whatsapp.Group:
        return self.session.bookmarks._whatsapp_group_info[self.legacy_id]

    async def fill_participants(self):
        info = await self.get_whatsapp_group_info()
        for ptr in info.Participants:
            data = whatsapp.GroupParticipant(handle=ptr)
            participant = await self.get_participant_by_contact(
                await self.session.contacts.by_legacy_id(data.JID)
            )
            if data.Affiliation == whatsapp.GroupAffiliationAdmin:
                participant.affiliation = "admin"
            elif data.Affiliation == whatsapp.GroupAffiliationOwner:
                participant.affiliation = "owner"

    async def update_info(self):
        info = await self.get_whatsapp_group_info()
        self.user_nick = info.Self.Name
        self.name = info.Name
        self.subject = info.Subject
        self.n_participants = len(info.Participants)

    async def backfill(self, oldest_id=None, oldest_date=None):
        pass


class Bookmarks(LegacyBookmarks[str, MUC]):
    session: "Session"

    def __init__(self, session: "Session"):
        super().__init__(session)
        self._whatsapp_group_info = dict[str, whatsapp.Group]()

    async def fill(self):
        groups = self.session.whatsapp.GetGroups()
        for ptr in groups:
            await self.add_whatsapp_group(whatsapp.Group(handle=ptr))

    async def add_whatsapp_group(self, data: whatsapp.Group):
        self._whatsapp_group_info[data.JID] = data
        await self.by_legacy_id(data.JID)

    async def legacy_id_to_jid_local_part(self, legacy_id: str):
        return await super().legacy_id_to_jid_local_part(
            "#" + legacy_id[: legacy_id.find("@")]
        )

    async def jid_local_part_to_legacy_id(self, local_part: str):
        if not local_part.startswith("#"):
            raise XMPPError(
                "item-not-found", "In slidge-whatsapp, group IDs start with a #"
            )
        # ideally, check that the group ID is valid in here and raise an appropriate XMPPError
        # if it's not the case.
        return await super().jid_local_part_to_legacy_id(
            local_part.removeprefix("#") + "@" + whatsapp.DefaultGroupServer
        )
