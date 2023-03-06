from typing import TYPE_CHECKING

from slidge import XMPPError
from slidge.core.muc import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from slidge.plugins.whatsapp.generated import whatsapp

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Participant(LegacyParticipant):
    contact: "Contact"
    muc: "MUC"

    def send_text(self, body, legacy_msg_id, **kw):
        super().send_text(body, legacy_msg_id, **kw)
        self.muc._sent[legacy_msg_id] = self.contact.legacy_id

    async def send_file(self, file_path, legacy_msg_id, **kw):
        await super().send_file(file_path, legacy_msg_id, **kw)
        self.muc._sent[legacy_msg_id] = self.contact.legacy_id


class MUC(LegacyMUC[str, str, Participant]):
    session: "Session"

    REACTIONS_SINGLE_EMOJI = True
    type = MucType.GROUP

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._sent = dict[str, str]()

    async def join(self, *a, **kw):
        await super().join(*a, **kw)
        avatar = self.session.whatsapp.GetAvatar(self.legacy_id, "")
        if avatar.URL:
            self.avatar = avatar.URL

    def get_message_sender(self, legacy_msg_id: str):
        sender_legacy_id = self._sent.get(legacy_msg_id)
        if sender_legacy_id is None:
            raise XMPPError("internal-server-error", "Unable to find message sender")
        return sender_legacy_id

    async def get_whatsapp_group_info(self) -> whatsapp.Group:
        try:
            return self.session.bookmarks._whatsapp_group_info[self.legacy_id]
        except KeyError:
            raise XMPPError("item-not-found", f"No group found for {self.legacy_id}")

    async def fill_participants(self):
        info = await self.get_whatsapp_group_info()
        participant = await self.get_user_participant()
        participant.contact = await self.session.contacts.by_legacy_id(info.Self.JID)
        if info.Self.Affiliation == whatsapp.GroupAffiliationAdmin:
            participant.affiliation = "admin"
        elif info.Self.Affiliation == whatsapp.GroupAffiliationOwner:
            participant.affiliation = "owner"
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
        self.n_participants = len(info.Participants) + 1


class Bookmarks(LegacyBookmarks[str, MUC]):
    session: "Session"

    def __init__(self, session: "Session"):
        super().__init__(session)
        self._whatsapp_group_info = dict[str, whatsapp.Group]()
        self.__filled = False

    async def fill(self):
        groups = self.session.whatsapp.GetGroups()
        for ptr in groups:
            await self.add_whatsapp_group(whatsapp.Group(handle=ptr))
        self.__filled = True

    async def add_whatsapp_group(self, data: whatsapp.Group):
        self._whatsapp_group_info[data.JID] = data
        await self.by_legacy_id(data.JID)

    async def legacy_id_to_jid_local_part(self, legacy_id: str):
        return await super().legacy_id_to_jid_local_part(
            "#" + legacy_id[: legacy_id.find("@")]
        )

    async def jid_local_part_to_legacy_id(self, local_part: str):
        if not local_part.startswith("#"):
            raise XMPPError("bad-request", "Invalid group ID, expected '#' prefix")

        if not self.__filled:
            raise XMPPError(
                "recipient-unavailable", "Still fetching group info, please retry later"
            )

        whatsapp_group_id = await super().jid_local_part_to_legacy_id(
            local_part.removeprefix("#") + "@" + whatsapp.DefaultGroupServer
        )

        if whatsapp_group_id not in self._mucs_by_legacy_id:
            raise XMPPError("item-not-found", f"No group found for {whatsapp_group_id}")

        return whatsapp_group_id
