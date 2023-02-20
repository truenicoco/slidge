from typing import TYPE_CHECKING, Optional

import emoji
from mattermost_api_reference_client.models import Status, User
from mattermost_api_reference_client.types import Unset

from slidge import LegacyContact, LegacyRoster

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session", str]):
    legacy_id: str

    MARKS = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._direct_channel_id: Optional[str] = None
        self._mm_id: Optional[str] = None

    def update_status(self, status: Optional[str]):
        if status is None:  # custom status
            self.session.log.debug("Status is None: %s", status)
            self.online()
        elif status == "online":
            self.online()
        elif status == "offline":
            self.offline()
        elif status == "away":
            self.away()
        elif status == "dnd":
            self.busy()
        else:
            self.session.log.warning(
                "Unknown status for '%s':",
                status,
            )

    async def direct_channel_id(self):
        if self._direct_channel_id is None:
            self._direct_channel_id = (
                await self.session.mm_client.get_direct_channel(await self.mm_id())
            ).id
            self.session.contacts.direct_channel_id_to_username[
                self._direct_channel_id
            ] = self.legacy_id
        return self._direct_channel_id

    async def mm_id(self):
        if self._mm_id is None:
            self._mm_id = (
                await self.session.mm_client.get_user_by_username(self.legacy_id)
            ).id
            self.session.contacts.user_id_to_username[self._mm_id] = self.legacy_id
        return self._mm_id

    async def update_reactions(self, legacy_msg_id):
        self.react(
            legacy_msg_id,
            [
                # TODO: find a better when than these non standard emoji aliases replace
                emoji.emojize(f":{x.replace('_3_', '_three_')}:", language="alias")
                for x in await self.session.get_mm_reactions(
                    legacy_msg_id, await self.mm_id()
                )
            ],
        )

    async def update_info(self, user: Optional[User] = None):
        if user is None:
            user = await self.session.mm_client.get_user(await self.mm_id())

        full_name = " ".join(
            filter(None, [user.first_name, user.last_name])  # type:ignore
        ).strip()

        self.name = user.nickname or full_name

        self.set_vcard(
            full_name=full_name,
            given=user.first_name,  # type:ignore
            surname=user.last_name,  # type:ignore
            email=user.email,  # type:ignore
        )
        self.avatar = await self.session.mm_client.get_profile_image(user.id)


class Roster(LegacyRoster["Session", Contact, str]):
    user_id_to_username: dict[str, str]
    direct_channel_id_to_username: dict[str, str]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.user_id_to_username = {}
        self.direct_channel_id_to_username = {}

    async def by_mm_user_id(self, user_id: str):
        try:
            legacy_id = self.user_id_to_username[user_id]
        except KeyError:
            user = await self.session.mm_client.get_user(user_id)
            if isinstance(user.username, Unset):
                raise RuntimeError
            legacy_id = self.user_id_to_username[user_id] = user.username
        return await self.by_legacy_id(legacy_id)

    async def by_direct_channel_id(self, channel_id: str):
        if (username := self.direct_channel_id_to_username.get(channel_id)) is None:
            for c in self:
                if (await c.direct_channel_id()) == channel_id:
                    return c
        else:
            return await self.by_legacy_id(username)

    async def fill(self):
        mm = self.session.mm_client
        user_ids = await mm.get_contacts()
        contact_mm_users = await mm.get_users_by_ids(user_ids)
        contact_mm_statuses = await mm.get_users_statuses_by_ids(user_ids)

        statuses = {s.user_id: s for s in contact_mm_statuses}

        for user in contact_mm_users:
            status: Status = statuses[user.id]
            contact = await self.by_legacy_id(user.username)
            await contact.add_to_roster()
            contact.update_status(str(status.status))
