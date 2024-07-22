"""
Handling groups
"""

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from slidge import LegacyBookmarks, LegacyMUC, LegacyParticipant, MucType
from slidge.util.types import Hat, HoleBound

if TYPE_CHECKING:
    from .session import Session


class Bookmarks(LegacyBookmarks):
    async def fill(self):
        for i in "aaa", "bbb":
            muc = await self.by_legacy_id(i)
            await muc.add_to_bookmarks()


class MUC(LegacyMUC):
    session: "Session"
    type = MucType.GROUP

    async def update_info(self):
        info = await self.session.legacy_client.get_group_info(self.legacy_id)
        self.name = info.name
        await self.set_avatar(info.avatar, info.avatar_unique_id)

    async def fill_participants(self):
        # in a real case, this would probably call something like
        # self.session.legacy_client.fetch_group_members(self.legacy_id)
        for i in 0, 111, 222:
            part = await self.get_participant_by_legacy_id(i)
            if i == 111:
                part.role = "moderator"
                part.affiliation = "owner"
                part.set_hats([Hat("test", "test"), Hat("prout", "prout")])
            yield part
        me = await self.get_user_participant()
        me.role = "moderator"
        me.affiliation = "owner"

    async def backfill(
        self,
        after: Optional[HoleBound] = None,
        before: Optional[HoleBound] = None,
    ):
        # in a real case, this would probably call something like
        # self.session.legacy_client.fetch_group_history(self.legacy_id)
        for i in range(10):
            part = await self.get_participant_by_legacy_id(0)
            part.send_text(
                f"History message #{i}",
                when=datetime.now() - timedelta(hours=i),
                legacy_msg_id=f"{i}--{uuid.uuid4().hex}",
                archive_only=True,
            )


class Participant(LegacyParticipant):
    pass
