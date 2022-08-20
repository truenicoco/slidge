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

import steam.enums
from steam.client import SteamClient
from steam.client.user import SteamUser
from steam.core.msg import MsgProto
from steam.enums.common import EPersonaState, EResult
from steam.enums.emsg import EMsg
from steam.protobufs.steammessages_friendmessages_pb2 import (
    k_EMessageReactionType_Emoticon,
)
from steam.steamid import SteamID

from slidge import *
from slidge.util import BiDict


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
    MARKS = False
    CORRECTION = False
    RETRACTION = False

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
    job_futures: dict[str, asyncio.Future[Any]]

    def post_init(self):
        store_dir = self.xmpp.home_dir / self.user.bare_jid
        store_dir.mkdir(exist_ok=True)

        self.job_futures = {}

        self.steam = SteamClient()
        self.steam.set_credential_location(store_dir)
        self.steam.username = self.user.registration_form["username"]

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(legacy_msg_id: Any) -> int:
        return int(legacy_msg_id)

    async def login(self):
        username = self.user.registration_form["username"]
        password = self.user.registration_form["password"]

        # self.steam.on(SteamClient.EVENT_CHAT_MESSAGE, self.on_steam_msg)
        self.steam.on(EMsg.ClientPersonaState, self.on_persona_state)
        self.steam.on("FriendMessagesClient.IncomingMessage#1", self.on_friend_message)
        self.steam.on("FriendMessagesClient.MessageReaction#1", self.on_friend_reaction)
        self.steam.on(EMsg.ServiceMethodResponse, self.on_service_method_response)

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

    def on_service_method_response(self, msg):
        self.log.debug("New service method response : %s", msg)
        try:
            fut = self.job_futures.pop(f"job_{msg.header.jobid_target}")
        except KeyError:
            self.log.debug(
                "Ignoring: %s vs %s", msg.header.jobid_target, self.job_futures
            )
        else:
            fut.set_result(msg.body)

    def on_friend_message(self, msg):
        self.log.debug("New friend message : %s", msg)
        if (type_ := msg.body.chat_entry_type) == steam.enums.EChatEntryType.Typing:
            user = self.steam.get_user(msg.body.steamid_friend)
            self.contacts.by_legacy_id(user.steam_id.id).composing()
        elif type_ == steam.enums.EChatEntryType.ChatMsg:
            user = self.steam.get_user(msg.body.steamid_friend)
            self.contacts.by_legacy_id(user.steam_id.id).send_text(
                msg.body.message, legacy_msg_id=msg.body.rtime32_server_timestamp
            )

    def on_friend_reaction(self, msg):
        self.log.debug("New friend reaction : %s", msg)
        if msg.body.reactor == self.steam.steam_id:
            pass
        else:
            if msg.body.reaction_type == k_EMessageReactionType_Emoticon:
                if msg.body.is_add:
                    # FIXME: this replace the XMPP reaction with the latest steam reaction
                    # we would need to fetch the friend's reaction list, but I did not find
                    # how to do that via this library
                    emoji = emoji_translate.get(msg.body.reaction, "❓")
                    self.contacts.by_legacy_id(SteamID(msg.body.reactor).id).react(
                        msg.body.server_timestamp, emoji
                    )
                else:
                    # FIXME: instead of retracting a single reaction, this deletes all reactions from the contact
                    # again, we need to retrieve the list of reactions for this message
                    self.contacts.by_legacy_id(SteamID(msg.body.reactor).id).react(
                        msg.body.server_timestamp, ""
                    )

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

    async def send_text(self, t: str, c: Contact, *, reply_to_msg_id=None):
        if not t:
            return
        job_id = self.steam.send_um(
            "FriendMessages.SendMessage#1",
            {
                "steamid": SteamID(c.legacy_id),
                "chat_entry_type": steam.enums.EChatEntryType.ChatMsg,
                "message": t,
            },
        )
        f = self.job_futures[job_id] = self.xmpp.loop.create_future()
        return (await f).server_timestamp

    async def send_file(self, u: str, c: Contact, *, reply_to_msg_id=None):
        return await self.send_text(u, c)

    async def active(self, c: Contact):
        pass

    async def inactive(self, c: Contact):
        pass

    async def composing(self, c: Contact):
        self.steam.send_um(
            "FriendMessages.SendMessage#1",
            {
                "steamid": SteamID(c.legacy_id),
                "chat_entry_type": steam.enums.EChatEntryType.Typing,
            },
        )

    async def paused(self, c: Contact):
        pass

    async def displayed(self, legacy_msg_id: Any, c: Contact):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: Contact):
        pass

    async def search(self, form_values: dict[str, str]):
        pass

    async def react(self, legacy_msg_id: Any, emojis: list[str], c: Contact):
        for emoji in emojis:
            emoji_name = emoji_translate.inverse.get(emoji)
            if emoji_name is None:
                self.send_gateway_message(
                    f"On steam, you can only react with {' '.join(emoji_translate.values())}"
                )
                continue
            self.steam.send_um(
                "FriendMessages.UpdateMessageReaction#1",
                {
                    "steamid": SteamID(c.legacy_id).as_64,
                    "server_timestamp": legacy_msg_id,
                    "reaction_type": k_EMessageReactionType_Emoticon,
                    "reaction": emoji_name,
                    "is_add": True,
                },
            )

    async def retract(self, legacy_msg_id: Any, c: Contact):
        pass


emoji_translate = BiDict[str, str](
    [
        (":steamthumbsup:", "👍"),
        (":steamthumbsdown:", "👎"),
        (":steambored:", "🥱"),
        (":steamfacepalm:", "🤦"),
        (":steamhappy:", "😄"),
        (":steammocking:", "😝"),
        (":steamsalty:", "🧂"),
        (":steamsad:", "😔"),
        (":steamthis:", "⬆️"),
    ]
)
