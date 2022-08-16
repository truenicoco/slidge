"""
Just a stab at steam chat.

Unfortunately the library underneath uses gevent and there is probably some work to do
to make it play nice with asyncio.

Right now, listing friends + send them messages works BUT in a blocking way.

Asyncsteampy https://github.com/somespecialone/asyncsteampy
might be interesting as it uses python's asyncio BUT the
login process seem a little too exotic for my taste.
"""
import asyncio
from typing import Any

from steam.client import SteamClient
from steam.client.user import SteamUser
from steam.core.msg import MsgProto
from steam.enums.common import EPersonaState, EResult
from steam.enums.emsg import EMsg
from steam.steamid import SteamID

from slidge import *


class Gateway(BaseGateway["Session"]):
    REGISTRATION_INSTRUCTIONS = "Enter steam credentials"
    REGISTRATION_FIELDS = [
        FormField(var="username", label="Steam username", required=True),
        FormField(var="password", label="Password", private=True, required=True),
    ]

    ROSTER_GROUP = "Steam"

    COMPONENT_NAME = "Steam (slidge)"
    COMPONENT_TYPE = "steam"

    COMPONENT_AVATAR = "https://logos-download.com/wp-content/uploads/2016/05/Steam_icon_logo_logotype.png"


class Contact(LegacyContact["Session"]):
    def update_status(self, persona_state: EPersonaState):
        if persona_state == EPersonaState.Offline:
            self.offline()
        elif persona_state == EPersonaState.Online:
            self.online()
        elif persona_state == EPersonaState.Busy:
            self.busy()
        elif persona_state == EPersonaState.Away:
            self.away()


class Roster(LegacyRoster[Contact, "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)


class Session(BaseSession[Contact, Roster, Gateway]):
    steam: SteamClient

    def post_init(self):
        store_dir = self.xmpp.home_dir / self.user.bare_jid
        store_dir.mkdir(exist_ok=True)

        self.steam = SteamClient()
        self.steam.set_credential_location(store_dir)
        self.steam.username = self.user.registration_form["username"]

    async def login(self):
        username = self.user.registration_form["username"]
        password = self.user.registration_form["password"]

        self.steam.on(SteamClient.EVENT_CHAT_MESSAGE, self.on_steam_msg)
        self.steam.on(EMsg.ClientPersonaState, self.on_persona_state)

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
            f: SteamUser
            self.log.debug("Friend: %s - %s - %s", f, f.name, f.steam_id.id)
            c = self.contacts.by_legacy_id(f.steam_id.id)
            c.name = f.name
            c.avatar = f.get_avatar_url()
            await c.add_to_roster()
            c.update_status(f.state)

        asyncio.create_task(self.idle())

    async def idle(self):
        while True:
            self.steam.idle()
            await asyncio.sleep(0.1)

    def on_steam_msg(self, user, text):
        self.log.debug("New message event: %s, %s", user, text)
        self.contacts.by_legacy_id(user.steam_id).send_text(text)

    def on_persona_state(self, msg: MsgProto):
        persona_state = msg.body
        self.log.debug("New state event: %s", persona_state)
        for f in persona_state.friends:
            if f.friendid == self.steam.steam_id:
                self.log.debug("This is me %s", self.steam.steam_id)
                return
            self.contacts.by_legacy_id(SteamID(f.friendid).id).update_status(
                f.persona_state
            )

    async def logout(self):
        pass

    async def send_text(self, t: str, c: LegacyContact):
        self.steam.get_user(c.legacy_id).send_message(t)

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
