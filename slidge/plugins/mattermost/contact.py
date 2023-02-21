import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from mattermost_api_reference_client.models import (
    Post,
    UpdateUserCustomStatusJsonBody,
    User,
)
from mattermost_api_reference_client.types import Unset

from slidge import LegacyContact, LegacyRoster

from .api import emojize

if TYPE_CHECKING:
    from .session import Session


class Contact(LegacyContact["Session", str]):
    legacy_id: str

    MARKS = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._direct_channel_id: Optional[str] = None
        self._mm_id: Optional[str] = None
        self._custom_status: Optional[UpdateUserCustomStatusJsonBody] = None
        self._custom_status_expires: Optional[datetime] = None
        self._last_mm_picture_update = None

    async def fetch_status(self):
        if not self.session.ws.ready.done():
            return
        i = await self.mm_id()
        status = await self.session.mm_client.get_user_status(i)
        self.update_status(status.status)

    def update_status(
        self,
        status: Optional[str] = None,
        custom_status: Optional[UpdateUserCustomStatusJsonBody] = None,
    ):
        if custom_status:
            self._custom_status = custom_status
            if expire_str := custom_status.expires_at:
                assert isinstance(expire_str, str)
                try:
                    self._custom_status_expires = datetime.fromisoformat(
                        expire_str[:-5]
                    )
                except ValueError:
                    pass

        if (when := self._custom_status_expires) and datetime.now() > when:
            self._custom_status = None
            self._custom_status_expires = None

        if c := self._custom_status:
            if c.emoji:
                e = emojize(c.emoji)
                parts = [e, c.text]
            else:
                parts = [c.text]
            text = " ".join(parts)
        else:
            text = None

        if status is None:  # custom status
            self.session.log.debug("Status is None: %s", status)
            self.online(text)
        elif status == "online":
            self.online(text)
        elif status == "offline":
            self.offline(text)
        elif status == "away":
            self.away(text)
        elif status == "dnd":
            self.busy(text)
        else:
            self.session.log.warning("Unknown status for '%s':", status)

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
            await self.session.get_mm_reactions(legacy_msg_id, await self.mm_id()),
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

        if self._last_mm_picture_update != user.last_picture_update:
            self.avatar = await self.session.mm_client.get_profile_image(user.id)

        self._last_mm_picture_update = user.last_picture_update

        props = user.props
        if not props:
            return

        custom = props.additional_properties.get("customStatus")  # type:ignore

        if not custom:
            return

        custom = UpdateUserCustomStatusJsonBody.from_dict(json.loads(custom))

        self.update_status(None, custom)

    async def send_mm_post(self, post: Post, carbon=False):
        assert not isinstance(post.metadata, Unset)
        assert not isinstance(post.update_at, Unset)

        file_metas = post.metadata.files
        text = post.message
        post_id = post.id

        when = datetime.fromtimestamp(post.update_at / 1000)

        assert isinstance(text, str)
        if not file_metas:
            self.send_text(
                text,
                legacy_msg_id=post_id,
                when=when,
                carbon=carbon,
            )
            return

        assert isinstance(file_metas, list)
        last_file_i = len(file_metas) - 1

        for i, file_meta in enumerate(file_metas):
            assert isinstance(file_meta.name, str)
            assert isinstance(file_meta.id, str)
            last = i == last_file_i
            await self.send_file(
                file_name=file_meta.name,
                data=await self.session.mm_client.get_file(file_meta.id),
                legacy_file_id=file_meta.id,
                legacy_msg_id=post_id if last else None,
                caption=text if last else None,
                carbon=carbon,
                when=when,
            )


class Roster(LegacyRoster["Session", Contact, str]):
    user_id_to_username: dict[str, str]
    direct_channel_id_to_username: dict[str, str]
    STATUS_POLL_INTERVAL = 300

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.user_id_to_username = {}
        self.direct_channel_id_to_username = {}

    async def by_jid(self, jid):
        c = await super().by_jid(jid)
        await c.fetch_status()
        return c

    async def by_legacy_id(self, legacy_id: str):
        c = await super().by_legacy_id(legacy_id)
        await c.fetch_status()
        return c

    async def update_statuses(self):
        while True:
            await asyncio.sleep(self.STATUS_POLL_INTERVAL)
            statuses = await self.session.ws.get_statuses()
            self.session.log.debug("Statuses: %s", statuses)
            for user_id, status in statuses.items():
                username = self.user_id_to_username.get(user_id)
                if username is None:
                    continue
                c = self._contacts_by_legacy_id.get(username)
                if c is not None and c.added_to_roster:
                    c.update_status(status)

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

        for user_id in user_ids:
            contact = await self.by_mm_user_id(user_id)
            await contact.add_to_roster()
