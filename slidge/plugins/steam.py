"""
Just a stab at steam chat.

Unfortunately the library underneath uses gevent and there is probably some work to do
to make it play nice with asyncio.

Right now, listing friends + send them messages works BUT in a blocking way.

Listening to events is broken.

Asyncsteampy https://github.com/somespecialone/asyncsteampy
might be interesting as it uses python's asyncio BUT the
login process seem a little too exotic for my taste.
"""

import pprint
from typing import Any, Optional

from slixmpp import Presence
from steam.client import SteamClient
from steam.core.msg import MsgProto
from steam.enums.common import EPersonaState, EResult

from slidge import *


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter steam credentials"
    REGISTRATION_FIELDS = [
        FormField(var="username", label="Steam username", required=True),
        FormField(var="password", label="Password", private=True, required=True),
    ]

    ROSTER_GROUP = "Steam"

    COMPONENT_NAME = "Steam (slidge)"
    COMPONENT_TYPE = "steam"

    COMPONENT_AVATAR = "https://logos-download.com/wp-content/uploads/2016/05/Steam_icon_logo_logotype.png"


class Roster(LegacyRoster):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


class Session(BaseSession[LegacyContact, Roster, Gateway]):
    steam: SteamClient

    def post_init(self):
        store_dir = self.xmpp.home_dir / self.user.bare_jid
        store_dir.mkdir(exist_ok=True)

        self.steam = SteamClient()
        self.steam.set_credential_location(store_dir)
        self.steam.username = self.user.registration_form["username"]
        self.steam.on(SteamClient.EVENT_CHAT_MESSAGE, self.on_steam_msg)

    async def login(self):
        username = self.user.registration_form["username"]
        password = self.user.registration_form["password"]

        login_result = self.steam.relogin()

        if login_result != EResult.OK:
            login_result = self.steam.login(username, password)

            if login_result == EResult.AccountLogonDenied:  # 2FA
                code = await self.input("Enter the code you received by email")
                login_result = self.steam.login(
                    self.user.registration_form["username"],
                    self.user.registration_form["password"],
                    auth_code=code,
                )

        self.log.debug("Login result: %s", login_result)
        if login_result == EResult.OK:
            self.log.debug("Login success")
        else:
            raise RuntimeError("Could not connect to steam")

        for f in self.steam.friends:
            self.log.debug("Friend: %s - %s", f, f.name)
            c = self.contacts.by_legacy_id(f.steam_id.id)
            c.name = f.name
            c.avatar = f.get_avatar_url()
            await c.add_to_roster()
            if f.state == EPersonaState.Online:
                c.online()
            elif f.state == EPersonaState.Busy:
                c.busy()
            elif f.state == EPersonaState.Away:
                c.away()
            elif f.state == EPersonaState.Offline:
                c.offline()
            else:
                self.log.warning("Unknown status: %s", f.state)

    def on_steam_msg(self, msg: MsgProto):
        self.log.debug("New message event: %s", vars(pprint.pformat(msg)))

    async def logout(self):
        pass

    async def send_text(self, t: str, c: LegacyContact):
        friend = self.steam.get_user(c.legacy_id)
        friend.send_message(t)
        self.steam.sleep(0)  # FIXME: implement this in a non blocking way

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

    async def search(self, form_values: dict[str, str]):
        pass
