import io
import logging
from typing import Optional

import aiohttp
from mattermost_api_reference_client.api.channels import (
    create_direct_channel,
    get_channel_members,
    get_channels_for_team_for_user,
)
from mattermost_api_reference_client.api.files import get_file, upload_file
from mattermost_api_reference_client.api.posts import (
    create_post,
    delete_post,
    get_posts_for_channel,
    update_post,
)
from mattermost_api_reference_client.api.status import get_users_statuses_by_ids
from mattermost_api_reference_client.api.teams import get_teams_for_user
from mattermost_api_reference_client.api.users import (
    get_profile_image,
    get_user,
    get_user_by_username,
    get_users_by_ids,
)
from mattermost_api_reference_client.client import AuthenticatedClient
from mattermost_api_reference_client.models import Status, User
from mattermost_api_reference_client.models.create_post_json_body import (
    CreatePostJsonBody,
)
from mattermost_api_reference_client.models.update_post_json_body import (
    UpdatePostJsonBody,
)
from mattermost_api_reference_client.models.upload_file_multipart_data import (
    UploadFileMultipartData,
)
from mattermost_api_reference_client.types import File, Unset


class MattermostClient:
    def __init__(self, *args, **kwargs):
        self.http = AuthenticatedClient(*args, **kwargs)
        self.mm_id: Optional[str] = None
        self.me: Optional[User] = None

    async def login(self):
        log.debug("Login")
        me = await get_user.asyncio("me", client=self.http)
        if me is None:
            raise RuntimeError("Could not login")
        self.me = me
        self.mm_id = my_id = me.id
        if isinstance(my_id, Unset):
            raise RuntimeError("Could not login")
        log.debug("Me: %s", me)

    async def get_contacts(self) -> list[str]:
        mm = self.http
        my_id = self.mm_id

        contact_mm_ids: list[str] = []

        teams = await get_teams_for_user.asyncio("me", client=mm)

        if teams is None:
            raise RuntimeError

        for team in teams:
            if isinstance(team.id, Unset):
                log.warning("Team without ID")
                continue
            channels = await get_channels_for_team_for_user.asyncio(
                "me", team.id, client=mm
            )

            if channels is None:
                log.warning("Team without channels")
                continue

            for channel in channels:
                if isinstance(channel.id, Unset):
                    log.warning("Channel without ID")
                    continue
                members = await self.get_channel_members(channel.id, per_page=4)
                if len(members) == 2:
                    user_ids = {m.user_id for m in members}
                    try:
                        user_ids.remove(my_id)
                    except KeyError:
                        log.warning("Weird 2 person channel: %s", members)
                    else:
                        contact_id = user_ids.pop()
                        if not isinstance(contact_id, str):
                            log.warning("Weird contact: %s", members)
                            continue
                        contact_mm_ids.append(contact_id)

        return contact_mm_ids

    async def get_channel_members(
        self, channel_id: str, *, page: int = 0, per_page: int = 10
    ):
        members = await get_channel_members.asyncio(
            channel_id, client=self.http, per_page=per_page, page=page
        )
        if members is None:
            raise RuntimeError
        return members

    async def get_users_by_ids(self, user_ids: list[str]) -> list[User]:
        r = await get_users_by_ids.asyncio(json_body=user_ids, client=self.http)
        if r is None:
            raise RuntimeError
        return r

    async def get_user(self, user_id: str):
        r = await get_user.asyncio(user_id, client=self.http)
        if r is None:
            raise RuntimeError
        if isinstance(r.username, Unset):
            raise RuntimeError
        return r

    async def get_users_statuses_by_ids(self, user_ids: list[str]) -> list[Status]:
        r = await get_users_statuses_by_ids.asyncio(
            json_body=user_ids, client=self.http
        )
        if r is None:
            raise RuntimeError
        return r

    async def send_message_to_user(self, user_id: str, text: str) -> str:
        mm = self.http

        other = await self.get_user_by_username(user_id)

        if self.mm_id is None:
            raise RuntimeError("Not logged?")

        direct_channel = await self.get_direct_channel(other.id)

        msg = await create_post.asyncio(
            json_body=CreatePostJsonBody(channel_id=direct_channel.id, message=text),
            client=mm,
        )
        if msg is None:
            raise RuntimeError

        if isinstance(msg.id, Unset):
            raise RuntimeError

        return msg.id

    async def send_message_with_file(self, channel_id: str, file_id: str):
        r = await create_post.asyncio(
            json_body=CreatePostJsonBody(
                channel_id=channel_id, file_ids=[file_id], message=""
            ),
            client=self.http,
        )
        if r is None or isinstance(r.id, Unset):
            raise RuntimeError(r)
        return r.id

    async def get_user_by_username(self, username: str) -> User:
        user = await get_user_by_username.asyncio(username, client=self.http)
        if user is None or isinstance(user.id, Unset):
            raise RuntimeError("Contact not found")
        return user

    async def get_direct_channel(self, user_id):
        direct_channel = await create_direct_channel.asyncio(
            json_body=[self.mm_id, user_id], client=self.http
        )
        if direct_channel is None or isinstance(direct_channel.id, Unset):
            raise RuntimeError("Could not create direct channel")
        return direct_channel

    async def get_profile_image(self, user_id: str) -> bytes:
        resp = await get_profile_image.asyncio_detailed(user_id, client=self.http)
        return resp.content

    async def get_file(self, file_id: str):
        resp = await get_file.asyncio_detailed(file_id, client=self.http)
        return resp.content

    async def delete_post(self, post_id: str):
        r = await delete_post.asyncio(post_id, client=self.http)
        if r is not None and not isinstance(r, Unset) and r.status != "ok":
            raise RuntimeError("Could not delete post %s", post_id)

    async def update_post(self, post_id: str, body: str):
        r = await update_post.asyncio(
            post_id,
            client=self.http,
            json_body=UpdatePostJsonBody(id=post_id, message=body),
        )
        if r is None or isinstance(r, Unset):
            raise RuntimeError(r)
        return r.id

    async def get_posts_for_channel(self, channel_id: str):
        r = await get_posts_for_channel.asyncio(
            channel_id, client=self.http, per_page=2
        )
        if r is None or isinstance(r, Unset):
            raise RuntimeError(r)
        return r

    async def upload_file(self, channel_id: str, url: str):
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as get_response:
                data = await get_response.read()
        req = UploadFileMultipartData(
            files=File(file_name=url.split("/")[-1], payload=io.BytesIO(data)),
            channel_id=channel_id,
        )
        r = await upload_file.asyncio(client=self.http, multipart_data=req)
        if (
            r is None
            or isinstance(r, Unset)
            or r.file_infos is None
            or isinstance(r.file_infos, Unset)
            or len(r.file_infos) != 1
        ):
            raise RuntimeError(r)
        return r.file_infos[0].id


log = logging.getLogger(__name__)
