from typing import TYPE_CHECKING, Optional, Union

import aiosignald.generated as sigapi
from slixmpp.exceptions import XMPPError
from slixmpp.jid import _unescape_node

from slidge import *
from slidge.core.contact import ESCAPE_TABLE

from .util import AttachmentSenderMixin

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Participant(AttachmentSenderMixin, LegacyParticipant):
    contact: "Contact"
    muc: "MUC"
    signal_address: sigapi.JsonAddressv1

    def send_text(self, body: str, legacy_msg_id=None, **k):
        if legacy_msg_id:
            self.muc.sent[legacy_msg_id] = self.signal_address
        super().send_text(body, legacy_msg_id, **k)


class MUC(LegacyMUC["Session", str, Participant, int]):
    REACTIONS_SINGLE_EMOJI = True

    session: "Session"

    type = MucType.GROUP

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = dict[int, sigapi.JsonAddressv1]()

    async def get_participants(self):
        group = await (await self.session.signal).get_group(
            account=self.session.phone, groupID=self.legacy_id
        )
        for m in group.members:
            if m.uuid == self.session.user_uuid:
                continue
            contact = await self.session.contacts.by_uuid(m.uuid)
            participant = await self.get_participant_by_contact(contact)
            yield participant

    async def get_participant_by_contact(self, contact):
        p = await self.get_participant(contact.name)
        p.contact = contact
        p.signal_address = contact.signal_address
        return p

    async def fill_history(self, *_, **__):
        pass


class Bookmarks(LegacyBookmarks["Session", MUC, str]):
    def __init__(self, session: "Session"):
        super().__init__(session)

        # maps case-insensitive JID local parts to case-sensitive signal group IDs
        self.known_groups = dict[str, str]()

    async def jid_local_part_to_legacy_id(self, local_part: str):
        try:
            return self.known_groups[_unescape_node(local_part.lower())]
        except KeyError:
            raise XMPPError("item-not-found", "I don't know this group")

    async def legacy_id_to_jid_local_part(self, legacy_id: str):
        local_part = legacy_id.lower().translate(ESCAPE_TABLE)
        self.known_groups[local_part] = legacy_id
        return local_part
