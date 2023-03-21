from datetime import datetime, timezone
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
        self._store(legacy_msg_id)

    async def send_file(self, file_path, legacy_msg_id, **kw):
        await super().send_file(file_path, legacy_msg_id, **kw)
        self._store(legacy_msg_id)

    def _store(self, legacy_msg_id: str):
        if self.is_user:
            self.muc.sent[legacy_msg_id] = str(self.session.contacts.user_legacy_id)
        else:
            self.muc.sent[legacy_msg_id] = self.contact.legacy_id


class MUC(LegacyMUC[str, str, Participant, str]):
    session: "Session"

    REACTIONS_SINGLE_EMOJI = True
    type = MucType.GROUP

    _ALL_INFO_FILLED_ON_STARTUP = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent = dict[str, str]()

    async def join(self, *a, **kw):
        await super().join(*a, **kw)
        try:
            avatar = self.session.whatsapp.GetAvatar(self.legacy_id, "")
        except RuntimeError:
            # no avatar
            pass
        else:
            if avatar.URL:
                self.avatar = avatar.URL

    def get_message_sender(self, legacy_msg_id: str):
        sender_legacy_id = self.sent.get(legacy_msg_id)
        if sender_legacy_id is None:
            raise XMPPError("internal-server-error", "Unable to find message sender")
        return sender_legacy_id

    async def update_whatsapp_info(self, info: whatsapp.Group):
        """
        Set MUC information based on WhatsApp group information, which may or may not be partial in
        case of updates to existing MUCs.
        """
        if info.Nickname:
            self.user_nick = info.Nickname
        if info.Name:
            self.name = info.Name
        if info.Subject.Subject or info.Subject.SetAt:
            self.subject = info.Subject.Subject
        if info.Subject.SetAt:
            set_at = datetime.fromtimestamp(info.Subject.SetAt, tz=timezone.utc)
            self.subject_date = set_at
        if info.Subject.SetByJID:
            contact = await self.session.contacts.by_legacy_id(info.Subject.SetByJID)
            self.subject_setter = contact.name
        for ptr in info.Participants:
            data = whatsapp.GroupParticipant(handle=ptr)
            participant = await self.get_participant_by_legacy_id(data.JID)
            if data.Action == whatsapp.GroupParticipantActionRemove:
                await self.remove_participant(participant)
            else:
                participant.affiliation = "member"
                if data.Affiliation == whatsapp.GroupAffiliationAdmin:
                    participant.affiliation = "admin"
                elif data.Affiliation == whatsapp.GroupAffiliationOwner:
                    participant.affiliation = "owner"


class Bookmarks(LegacyBookmarks[str, MUC]):
    session: "Session"

    def __init__(self, session: "Session"):
        super().__init__(session)
        self.__filled = False

    async def fill(self):
        groups = self.session.whatsapp.GetGroups()
        for ptr in groups:
            await self.add_whatsapp_group(whatsapp.Group(handle=ptr))
        self.__filled = True

    async def add_whatsapp_group(self, data: whatsapp.Group):
        muc = await self.by_legacy_id(data.JID)
        await muc.update_whatsapp_info(data)

    async def legacy_id_to_jid_local_part(self, legacy_id: str):
        return "#" + legacy_id[: legacy_id.find("@")]

    async def jid_local_part_to_legacy_id(self, local_part: str):
        if not local_part.startswith("#"):
            raise XMPPError("bad-request", "Invalid group ID, expected '#' prefix")

        if not self.__filled:
            raise XMPPError(
                "recipient-unavailable", "Still fetching group info, please retry later"
            )

        whatsapp_group_id = (
            local_part.removeprefix("#") + "@" + whatsapp.DefaultGroupServer
        )

        if whatsapp_group_id not in self._mucs_by_legacy_id:
            raise XMPPError("item-not-found", f"No group found for {whatsapp_group_id}")

        return whatsapp_group_id
