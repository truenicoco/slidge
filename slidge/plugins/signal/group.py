import base64
import binascii
from pathlib import Path
from typing import TYPE_CHECKING

import aiosignald.exc as sigexc
import aiosignald.generated as sigapi

from slidge import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType, XMPPError

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


class MUC(LegacyMUC[str, int, Participant, str]):
    REACTIONS_SINGLE_EMOJI = True

    session: "Session"

    type = MucType.GROUP

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = dict[int, sigapi.JsonAddressv1]()
        # keys = msg timestamp; vals = single character emoji
        self.user_reactions = dict[int, str]()

    async def get_signal_group(self) -> sigapi.JsonGroupV2Infov1:
        return await (await self.session.signal).get_group(
            account=self.session.phone, groupID=self.legacy_id
        )

    async def fill_participants(self):
        group = await self.get_signal_group()
        for m in group.members:
            if m.uuid == await self.session.user_uuid:
                await self.get_user_participant()
                continue
            contact = await self.session.contacts.by_uuid(m.uuid)
            await self.get_participant_by_contact(contact)

    async def get_participant_by_contact(self, contact):
        p = await super().get_participant_by_contact(contact)
        p.signal_address = contact.signal_address
        return p

    async def update_info(self):
        group = await self.get_signal_group()
        self.DISCO_NAME = group.title
        self.subject = group.description
        self.description = group.description
        self.n_participants = len(group.members)
        if path := group.avatar:
            self.avatar = Path(path)


class Bookmarks(LegacyBookmarks[str, MUC]):
    session: "Session"

    async def jid_local_part_to_legacy_id(self, local_part: str):
        try:
            group_id = local_part_to_group_id(local_part)
        except binascii.Error:
            raise XMPPError(
                "bad-request", "This is not a valid base32 encoded signal group ID"
            )

        signal = await self.session.signal
        try:
            await signal.get_group(account=self.session.phone, groupID=group_id)
        except (sigexc.InvalidGroupError, sigexc.UnknownGroupError) as e:
            raise XMPPError("item-not-found", e.message)
        except sigexc.SignaldException as e:
            raise XMPPError("internal-server-error", str(e))

        return group_id

    async def legacy_id_to_jid_local_part(self, legacy_id: str):
        return group_id_to_local_part(legacy_id)

    async def fill(self):
        session = self.session
        groups = await (await session.signal).list_groups(account=session.phone)
        self.log.debug("GROUPS: %r", groups)
        for group in groups.groups:
            await self.by_legacy_id(group.id)


def local_part_to_group_id(s: str):
    return base64.b32decode(bytes(s.upper(), "utf-8")).decode()


def group_id_to_local_part(s: str):
    return base64.b32encode(bytes(s, "utf-8")).decode().lower()
