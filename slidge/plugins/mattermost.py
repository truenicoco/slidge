import asyncio
import concurrent.futures
import json
import pprint
from typing import Dict, Any, Optional, Hashable

import requests
from mattermostdriver import Driver, Websocket

from slixmpp import JID, Presence

from slidge import *
from slidge.legacy.contact import LegacyContact


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter skype credentials"
    REGISTRATION_FIELDS = [
        FormField(var="url", label="Mattermost server URL", required=True),
        FormField(var="login_id", label="User name"),
        FormField(var="password", label="Password", private=True),
        FormField(var="token", label="Personal access token"),
        FormField(var="scheme", label="HTTP scheme", value="https"),
        FormField(var="port", label="port", value="8065"),
        FormField(var="basepath", label="Base path", value="/api/v4"),
        FormField(
            var="verify", label="Strict SSL verification", value="true", type="boolean"
        ),
        FormField(var="mfa_token", label="MFA token"),
    ]

    ROSTER_GROUP = "Mattermost"

    COMPONENT_NAME = "Mattermost (slidge)"
    COMPONENT_TYPE = "mattermost"

    COMPONENT_AVATAR = "https://play-lh.googleusercontent.com/aX7JaAPkmnkeThK4kgb_HHlBnswXF0sPyNI8I8LNmEMMo1vDvMx32tCzgPMsyEXXzZRc"


class Session(BaseSession[LegacyContact, LegacyRoster]):
    mm: Driver
    ws: Optional[Websocket]

    def post_init(self):
        self.mm = Driver(
            self.user.registration_form
            | {"port": int(self.user.registration_form["port"])}
        )
        self.ws = None

    async def async_wrap(self, func, *args):
        return await self.xmpp.loop.run_in_executor(executor, func, *args)

    async def login(self, p: Presence):
        self.log.debug("Login")

        me = await self.async_wrap(self.mm.login)
        self.log.debug("Me: %s", me)
        teams = await self.async_wrap(self.mm.teams.get_user_teams, me["id"])
        # self.log.debug("My teams: %s", teams)
        for t in teams:
            channels = await self.async_wrap(self.mm.channels.get_channels_for_user, me["id"], t["id"])
            for channel in channels:
                members = await self.async_wrap(self.mm.channels.get_channel_members, channel["id"])
                if len(members) == 2:
                    me_found = False
                    contact_mm = None
                    for m in members:
                        if m["user_id"] == me["id"]:
                            me_found = True
                        else:
                            contact_mm = self.mm.users.get_user(m["user_id"])
                    if not me_found or contact_mm is None:
                        self.log.debug("Weird 2 person channel: %s", members)
                        continue

                    # details = await self.async_wrap(self.mm.channels.get_channel, channel["id"])
                    self.log.debug("Contact: %s", contact_mm)
                    contact = self.contacts.by_legacy_id(contact_mm["username"])
                    if contact_mm["nickname"]:
                        contact.name = contact_mm["nickname"]
                    else:
                        contact.name = contact_mm["first_name"] + " " + contact_mm["last_name"]
                    await contact.add_to_roster()
                    img_url: requests.Response = self.mm.users.get_user_profile_image(contact_mm["id"])

                    contact.avatar = img_url.content

                    if "props" in contact_mm and "customStatus" in contact_mm["props"]:
                        self.log.debug("Custom status: %s", pprint.pformat(contact_mm))
                        status_str = contact_mm["props"]["customStatus"]
                        if status_str:
                            status = json.loads(contact_mm["props"]["customStatus"])
                            contact.status(status["text"] + " - " + status["emoji"])
                    else:
                        contact.online()

        self.ws = Websocket(self.mm.options, self.mm.client.token)

        asyncio.create_task(self.ws.connect(self.on_mm_event))

    async def on_mm_event(self, event):
        self.log.debug("Event: %s", event)

    async def logout(self, p: Optional[Presence]):
        pass

    async def send_text(self, t: str, c: LegacyContact):
        pass

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


executor = (
    concurrent.futures.ThreadPoolExecutor()
)  # TODO: close this gracefully on exit
