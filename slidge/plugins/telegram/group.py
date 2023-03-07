import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import aiotdlib.api as tgapi

from slidge import *

from . import config
from .util import AvailableEmojisMixin, TelegramToXMPPMixin

if TYPE_CHECKING:
    from .contact import Contact
    from .session import Session


class Bookmarks(LegacyBookmarks[int, "MUC"]):
    session: "Session"

    # COMPAT: We prefix with 'group' because movim does not like MUC local parts
    #         starting with a hyphen

    @staticmethod
    async def legacy_id_to_jid_local_part(legacy_id: int):
        return "group" + str(legacy_id)

    async def jid_local_part_to_legacy_id(self, local_part: str):
        try:
            group_id = int(local_part.replace("group", ""))
        except ValueError:
            raise XMPPError(
                "bad-request",
                "This does not look like a valid telegram ID, at least not for slidge. "
                "Do not be like edhelas, do not attempt to join groups you had joined "
                "through spectrum. ",
            )
        try:
            info = await self.session.tg.get_chat_info(group_id)
        except tgapi.BadRequest as e:
            raise XMPPError(
                "bad-request",
                f"Not a valid telegram group ID: {group_id} ({e.message})",
            )
        if isinstance(info, (tgapi.User, tgapi.UserFullInfo, tgapi.SecretChat)):
            raise XMPPError(
                "bad-request", f"This is not a telegram group, but a {type(info)}"
            )
        return group_id

    async def fill(self):
        tg = self.session.tg
        for chat in await tg.get_main_list_chats_all():
            if isinstance(
                chat.type_, (tgapi.ChatTypeBasicGroup, tgapi.ChatTypeSupergroup)
            ):
                await self.by_legacy_id(chat.id)


class MUC(AvailableEmojisMixin, LegacyMUC[int, int, "Participant"]):
    MAX_SUPER_GROUP_PARTICIPANTS = 200
    session: "Session"
    # all group chats in telegram correspond are closer to modern XMPP 'groups' than 'channels'
    type = MucType.GROUP

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.chat_id = self.legacy_id
        #                                     tuple[participant, emoji]
        self.reactions = defaultdict[int, set[tuple[Participant, str]]](set)
        self.session.xmpp.loop.create_task(self.update_subject_from_msg())

    async def update_info(self):
        tg = self.session.tg
        chat = await tg.get_chat(self.legacy_id)
        if isinstance(chat.type_, tgapi.ChatTypeBasicGroup):
            group = await tg.get_basic_group(chat.type_.basic_group_id)
            info = await tg.get_basic_group_full_info(group.id)
        elif isinstance(chat.type_, tgapi.ChatTypeSupergroup):
            group = await tg.get_supergroup(chat.type_.supergroup_id)
            info = await tg.get_supergroup_full_info(group.id)
        else:
            raise XMPPError("bad-request", f"This is not a telegram group: {chat}")
        if photo := info.photo:
            best = min(photo.sizes, key=lambda x: x.width).photo
            file = await tg.api.download_file(
                best.id,
                priority=32,
                offset=0,
                limit=0,
                skip_validation=True,
                synchronous=True,
            )
            self.avatar = Path(file.local.path)
        self.n_participants = group.member_count
        self.name = self.description = chat.title

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
                self.subject_setter = self.user_nick_non_none
            else:
                contact = await self.session.contacts.by_legacy_id(sender_id.user_id)
                self.subject_setter = contact.name
        else:
            self.subject_setter = self.name

        if isinstance(content, tgapi.MessagePhoto):
            self.subject = content.caption.text
        if isinstance(content, tgapi.MessageText):
            self.subject = content.text.text

    async def fill_participants(self):
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
            await self.participant_by_tg_user(
                await self.session.tg.get_user(sender.user_id)
            )

    async def send_text(self, text: str) -> int:
        result = await self.session.tg.send_text(self.legacy_id, text)
        self.log.debug("MUC SEND RESULT: %s", result)
        msg_id = await self.session.wait_for_tdlib_success(result.id)
        self.log.debug("MUC SEND MSG: %s", msg_id)
        return msg_id

    async def participant_by_tg_user(self, user: tgapi.User) -> "Participant":
        if user.id == await self.session.tg.get_my_id():
            return await self.get_user_participant()
        return await self.get_participant_by_legacy_contact_id(user.id)

    async def participant_by_tg_user_id(self, user_id: int) -> "Participant":
        if user_id == await self.session.tg.get_my_id():
            return await self.get_user_participant()
        return await self.participant_by_tg_user(
            await self.session.tg.api.get_user(user_id)
        )

    async def get_tg_chat(self):
        return await self.session.tg.get_chat(self.legacy_id)

    async def backfill(self, oldest_message_id=None, oldest_date=None):
        for m in await self.fetch_history(
            config.GROUP_HISTORY_MAXIMUM_MESSAGES, oldest_message_id
        ):
            part = await self.participant_by_sender_id(m.sender_id)
            await part.send_tg_message(m, archive_only=True)

    async def fetch_history(self, n: int, before: Optional[int] = None):
        tg = self.session.tg
        chat = await self.get_tg_chat()
        m = chat.last_message
        if m is None:
            return []

        messages = [chat.last_message]
        i = 0
        last_message_id = m.id
        while True:
            try:
                fetched = (
                    await tg.api.get_chat_history(
                        chat_id=self.legacy_id,
                        from_message_id=last_message_id,
                        offset=0,
                        limit=10,
                        only_local=False,
                    )
                ).messages
            except asyncio.TimeoutError:
                self.log.warning(
                    "Timeout while trying to fetch chat history for %s. "
                    "We could only fetch %s message",
                    self,
                    len(messages),
                )
                break
            if len(fetched) == 0:
                break
            messages.extend(fetched)
            i += len(fetched)
            if i > n:
                break

            last_message_id = fetched[-1].id

        messages = messages[::-1]
        if before is not None:
            try:
                i = [m.id for m in messages].index(before)
            except ValueError:
                self.log.warning("Did not find oldest message in archive")
            else:
                messages = messages[:i]

        return messages

    async def participant_by_sender_id(self, sender_id: tgapi.MessageSender):
        if isinstance(sender_id, tgapi.MessageSenderUser):
            return await self.participant_by_tg_user(
                await self.session.tg.api.get_user(sender_id.user_id)
            )
        else:
            return self.get_system_participant()


class Participant(LegacyParticipant, TelegramToXMPPMixin):
    contact: "Contact"
    session: "Session"
    muc: "MUC"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.chat_id = self.muc.legacy_id

    def __hash__(self):
        if self.is_user:
            return 0
        return self.contact.legacy_id
