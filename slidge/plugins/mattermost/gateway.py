import json
import pprint
import re
from datetime import datetime
from typing import Any, Dict, Optional

from mattermost_api_reference_client.models import Status
from mattermost_api_reference_client.types import Unset
from slidge import *
from slixmpp import Presence

from .api import MattermostClient
from .websocket import EventType, MattermostEvent, Websocket


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = (
        "Enter mattermost credentials. "
        "Get your MMAUTH_TOKEN on the web interface, using the dev tools of your browser (it's a cookie)."
    )
    REGISTRATION_FIELDS = [
        FormField(var="url", label="Mattermost server URL", required=True),
        FormField(var="token", label="MMAUTH_TOKEN", required=True),
        FormField(var="basepath", label="Base path", value="/api/v4", required=True),
        FormField(
            var="basepath_ws",
            label="Websocket base path",
            value="/websocket",
            required=True,
        ),
        FormField(
            var="strict_ssl",
            label="Strict SSL verification",
            value="1",
            required=True,
            type="boolean",
        ),
    ]

    ROSTER_GROUP = "Mattermost"

    COMPONENT_NAME = "Mattermost (slidge)"
    COMPONENT_TYPE = "mattermost"

    COMPONENT_AVATAR = "https://play-lh.googleusercontent.com/aX7JaAPkmnkeThK4kgb_HHlBnswXF0sPyNI8I8LNmEMMo1vDvMx32tCzgPMsyEXXzZRc"


class Contact(LegacyContact):
    legacy_id: str


class Roster(LegacyRoster[Contact]):
    user_id_to_username: dict[str, str] = {}
    channel_id_to_username: dict[str, str] = {}
    session: "Session"

    async def by_mm_user_id(self, user_id: str):
        try:
            legacy_id = self.user_id_to_username[user_id]
        except KeyError:
            user = await self.session.mm_client.get_user(user_id)
            if isinstance(user.username, Unset):
                raise RuntimeError
            legacy_id = self.user_id_to_username[user_id] = user.username
        return self.by_legacy_id(legacy_id)


class Session(BaseSession[Contact, Roster]):
    mm_client: MattermostClient
    ws: Websocket
    messages_waiting_for_echo: set[str]

    def post_init(self):
        self.messages_waiting_for_echo = set()
        f = self.user.registration_form
        url = f["url"] + f["basepath"]
        self.mm_client = MattermostClient(
            url,
            verify_ssl=f["strict_ssl"],
            timeout=5,
            token=f["token"],
        )
        self.ws = Websocket(
            re.sub("^http", "ws", f["url"]) + f["basepath"] + f["basepath_ws"],
            f["token"],
        )

    async def login(self, p: Presence):
        await self.mm_client.login()

        await self.add_contacts()
        await self.ws.connect(self.on_mm_event)

    async def add_contacts(self):
        user_ids = await self.mm_client.get_contacts()
        contact_mm_users = await self.mm_client.get_users_by_ids(user_ids)
        contact_mm_statuses = await self.mm_client.get_users_statuses_by_ids(user_ids)

        statuses = {s.user_id: s for s in contact_mm_statuses}

        for user in contact_mm_users:
            status: Status = statuses[user.id]
            contact = self.contacts.by_legacy_id(user.username)
            self.contacts.user_id_to_username[user.id] = user.username
            if user.nickname:
                contact.name = user.nickname
            else:
                contact.name = user.first_name + " " + user.last_name

            contact.avatar = await self.mm_client.get_profile_image(user.id)

            await contact.add_to_roster()
            if status.status is None:  # custom status
                self.log.debug("Weird status: %s", status)
                contact.online()
            elif status.status == "online":
                contact.online()
            elif status.status == "offline":
                contact.offline()
            elif status.status == "away":
                contact.away()
            elif status.status == "dnd":
                contact.busy()
            else:
                self.log.warning(
                    "Unknown status for '%s': '%s'",
                    user.username,
                    status.status,
                )

    async def on_mm_event(self, event: MattermostEvent):
        self.log.debug("Event: %s", event)
        if event.type == EventType.Hello:
            self.log.info("Received hello event: %s", event.data)
        elif event.type == EventType.Posted:
            post = json.loads(event.data["post"])
            self.log.debug("Post: %s", pprint.pformat(post))

            message = post["message"]

            channel_id = post["channel_id"]
            post_id = post["id"]
            user_id = post["user_id"]

            if event.data["channel_type"] == "D":  # Direct messages?
                if user_id == self.mm_client.mm_id:
                    try:
                        self.messages_waiting_for_echo.remove(post_id)
                    except KeyError:
                        members = await self.mm_client.get_channel_members(channel_id)
                        if len(members) > 2:
                            raise RuntimeError("Not a direct message after all")
                        for m in members:
                            if m.user_id != self.mm_client.mm_id:
                                contact = await self.contacts.by_mm_user_id(m.user_id)
                                break
                        else:
                            raise RuntimeError("What?")

                        contact.carbon(
                            message,
                            post_id,
                            datetime.fromtimestamp(post["update_at"] / 1000),
                        )
                else:
                    contact = await self.contacts.by_mm_user_id(user_id)
                    contact.send_text(message)
            elif event.data["channel_type"] == "P":
                # private channel
                pass

        elif event.type == EventType.ChannelViewed:
            pass

    async def logout(self, p: Optional[Presence]):
        pass

    async def send_text(self, t: str, c: Contact):
        msg_id = await self.mm_client.send_message_to_user(c.legacy_id, t)
        self.messages_waiting_for_echo.add(msg_id)
        return msg_id

    async def send_file(self, u: str, c: LegacyContact):
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        pass

    async def paused(self, c: LegacyContact):
        pass

    async def displayed(self, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def search(self, form_values: Dict[str, str]):
        pass
