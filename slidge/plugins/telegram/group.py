from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiotdlib.api as tgapi
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *

from .util import AvailableEmojisMixin, TelegramToXMPPMixin

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def legacy_id_to_jid_local_part(legacy_id: int):
        return "group" + str(legacy_id)

    @staticmethod
    async def jid_local_part_to_legacy_id(local_part: str):
        return int(local_part.replace("group", ""))


class MUC(LegacyMUC["Session", int, "Participant", int], AvailableEmojisMixin):
    MAX_SUPER_GROUP_PARTICIPANTS = 200
    session: "Session"
    name = "unnamed"

    async def join(self, join_presence):
        self.user_nick = await self.session.my_name
        await self.update_subject_from_msg()
        await super().join(join_presence)

    async def update_subject_from_msg(self, msg: Optional[tgapi.Message] = None):
        if msg is None:
            try:
                msg = await self.session.tg.api.get_chat_pinned_message(self.legacy_id)
                self.log.debug("Pinned message: %s", type(msg.content))
            except tgapi.NotFound:
                self.log.debug("Pinned message not found?")
                return
        content = msg.content
        if not isinstance(content, (tgapi.MessagePhoto, tgapi.MessageText)):
            return

        sender_id = msg.sender_id
        self.subject_date = datetime.fromtimestamp(msg.date, tz=timezone.utc)
        if isinstance(sender_id, tgapi.MessageSenderUser):
            if sender_id.user_id == await self.session.tg.get_my_id():
                self.subject_setter = self.user_nick
            else:
                contact = await self.session.contacts.by_legacy_id(sender_id.user_id)
                self.subject_setter = contact.name
        else:
            self.subject_setter = self.name

        if isinstance(content, tgapi.MessagePhoto):
            self.subject = content.caption.text
        if isinstance(content, tgapi.MessageText):
            self.subject = content.text.text

    async def get_participants(self):
        self.log.debug("Getting participants")
        chat = await self.session.tg.get_chat(chat_id=self.legacy_id)
        if not isinstance(
            chat.type_, (tgapi.ChatTypeBasicGroup, tgapi.ChatTypeSupergroup)
        ):
            raise XMPPError("item-not-found", text="This is not a valid group ID")

        info = await self.session.tg.get_chat_info(chat, full=True)
        if isinstance(info, tgapi.BasicGroupFullInfo):
            members = info.members
        elif isinstance(info, tgapi.SupergroupFullInfo):
            if info.can_get_members:
                members = (
                    await self.session.tg.api.get_supergroup_members(
                        supergroup_id=chat.type_.supergroup_id,
                        filter_=None,
                        offset=0,
                        limit=self.MAX_SUPER_GROUP_PARTICIPANTS,
                        skip_validation=True,
                    )
                ).members
            else:
                members = []
        else:
            raise RuntimeError
        self.log.debug("%s participants", len(members))
        for member in members:
            sender = member.member_id
            if not isinstance(sender, tgapi.MessageSenderUser):
                self.log.debug("Ignoring non-user sender")  # Does this happen?
                continue
            if sender.user_id == await self.session.tg.get_my_id():
                continue
            yield await self.participant_by_tg_user(
                await self.session.tg.get_user(sender.user_id)
            )

    async def send_text(self, text: str) -> int:
        result = await self.session.tg.send_text(self.legacy_id, text)
        self.log.debug("MUC SEND RESULT: %s", result)
        msg_id = await self.session.wait_for_tdlib_success(result.id)
        self.log.debug("MUC SEND MSG: %s", msg_id)
        return msg_id

    async def participant_by_tg_user(self, user: tgapi.User) -> "Participant":
        return await Participant.by_tg_user(self, user)

    async def participant_system(self) -> "Participant":
        return await self.get_participant("")

    async def participant_by_tg_user_id(self, user_id: int) -> "Participant":
        return await Participant.by_tg_user(
            self, await self.session.tg.api.get_user(user_id)
        )

    async def get_tg_chat(self):
        return await self.session.tg.get_chat(self.legacy_id)

    async def fill_history(
        self,
        full_jid: JID,
        maxchars: Optional[int] = None,
        maxstanzas: Optional[int] = None,
        seconds: Optional[int] = None,
        since: Optional[int] = None,
    ):
        for m in await self.fetch_history(50):
            part = await self.participant_by_tg_user(
                await self.session.tg.get_user(m.sender_id.user_id)
            )
            await part.send_tg_message(m, full_jid=full_jid)

    async def fetch_history(self, n: int):
        tg = self.session.tg
        chat = await self.get_tg_chat()
        m = chat.last_message
        if m is None:
            return []

        messages = [chat.last_message]
        i = 0
        last_message_id = m.id
        while True:
            fetched = (
                await tg.api.get_chat_history(
                    chat_id=self.legacy_id,
                    from_message_id=last_message_id,
                    offset=0,
                    limit=10,
                    only_local=False,
                )
            ).messages
            if len(fetched) == 0:
                break
            messages.extend(fetched)
            i += len(fetched)
            if i > n:
                break

            last_message_id = fetched[-1].id

        return reversed(messages)


class Participant(LegacyParticipant[MUC], TelegramToXMPPMixin):
    contact: "Contact"
    session: "Session"  # type:ignore

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.chat_id = self.muc.legacy_id
        self.session.log.debug("PARTICIPANT-N: %s", self.muc.n_participants)

    @staticmethod
    async def by_tg_user(muc: MUC, user: tgapi.User):
        nick = " ".join((user.first_name, user.last_name)).strip()
        p = Participant(muc, nick)
        p.contact = await muc.session.contacts.by_legacy_id(user.id)
        return p
