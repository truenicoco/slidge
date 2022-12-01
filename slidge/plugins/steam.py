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
from collections import defaultdict
from functools import partial
from typing import Any, Callable

import steam.enums
from slixmpp.exceptions import XMPPError
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


class Contact(LegacyContact["Session", int]):
    MARKS = False
    CORRECTION = False
    RETRACTION = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # keys = msg timestamp; vals = list of single character emoji
        self.user_reactions = defaultdict[int, set[str]](set)
        self.contact_reactions = defaultdict[int, set[str]](set)

    def update_status(self, persona_state: EPersonaState):
        if persona_state == EPersonaState.Offline:
            self.offline()
        elif persona_state == EPersonaState.Online:
            self.online()
        elif persona_state == EPersonaState.Busy:
            self.busy()
        elif persona_state == EPersonaState.Away:
            self.away()

    def update_reactions(self, timestamp: int):
        self.react(timestamp, self.contact_reactions[timestamp])


class Roster(LegacyRoster["Session", Contact, int]):
    async def jid_username_to_legacy_id(self, jid_username: str) -> int:
        try:
            return int(jid_username)
        except ValueError:
            raise XMPPError("bad-request")

    def by_steam_user(self, steam_user: SteamUser) -> asyncio.Task[Contact]:
        return self.by_steam_id(steam_user.steam_id)

    def by_steam_id(self, steam_id: SteamID) -> asyncio.Task[Contact]:
        return self.session.xmpp.loop.create_task(self.by_legacy_id(steam_id.id))

    def by_steam_user_apply(self, steam_user: SteamUser, method: Callable):
        task = self.by_steam_user(steam_user)
        task.add_done_callback(lambda f: method(f.result()))

    def by_steam_id_apply(self, steam_id: SteamID, method: Callable):
        task = self.by_steam_id(steam_id)
        task.add_done_callback(lambda f: method(f.result()))


class Session(BaseSession[Gateway, int, Roster, Contact]):
    def __init__(self, user):
        super().__init__(user)
        store_dir = global_config.HOME_DIR / self.user.bare_jid
        store_dir.mkdir(exist_ok=True)

        self.job_futures = dict[str, asyncio.Future[Any]]()

        self.steam = SteamClient()
        self.steam.set_credential_location(store_dir)
        self.steam.username = self.user.registration_form["username"]

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(xmpp_msg_id: str):
        return int(xmpp_msg_id)

    async def login(self):
        username = self.user.registration_form["username"]
        password = self.user.registration_form["password"]

        # self.steam.on(SteamClient.EVENT_CHAT_MESSAGE, self.on_steam_msg)
        self.steam.on(EMsg.ClientPersonaState, self.on_persona_state)
        self.steam.on("FriendMessagesClient.IncomingMessage#1", self.on_friend_message)
        self.steam.on("FriendMessagesClient.MessageReaction#1", self.on_friend_reaction)
        self.steam.on(EMsg.ServiceMethodResponse, self.on_service_method_response)

        login_result = self.steam.relogin()

        self.log.debug("Re-login result: %s", login_result)

        if login_result != EResult.OK:
            login_result = self.steam.login(username, password)

            self.log.debug("Login result: %s", login_result)

            if login_result == EResult.AccountLogonDenied:
                # 2FA by mail (?)
                code = await self.input("Enter the code you received by email")
                login_result = self.steam.login(
                    self.user.registration_form["username"],
                    self.user.registration_form["password"],
                    auth_code=code,
                )
            elif login_result == EResult.AccountLoginDeniedNeedTwoFactor:
                # steam guard (?)
                code = await self.input("Enter your 2FA code")
                login_result = self.steam.login(
                    self.user.registration_form["username"],
                    self.user.registration_form["password"],
                    two_factor_code=code,
                )

        self.log.debug("Login result: %s", login_result)
        if login_result == EResult.OK:
            self.log.debug("Login success")
        else:
            raise RuntimeError("Could not connect to steam")

        for f in self.steam.friends:
            self.log.debug("Friend: %s - %s - %s", f, f.name, f.steam_id.id)
            c = await self.contacts.by_legacy_id(f.steam_id.id)
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
        steam_user = self.steam.get_user(msg.body.steamid_friend)
        if (type_ := msg.body.chat_entry_type) == steam.enums.EChatEntryType.Typing:
            self.contacts.by_steam_user_apply(steam_user, Contact.composing)
        elif type_ == steam.enums.EChatEntryType.ChatMsg:
            self.contacts.by_steam_user_apply(
                steam_user,
                partial(
                    Contact.send_text,
                    body=msg.body.message,
                    legacy_msg_id=msg.body.rtime32_server_timestamp,
                ),
            )

    def on_friend_reaction(self, msg):
        self.log.debug("New friend reaction : %s", msg)
        body = msg.body
        timestamp = body.server_timestamp
        emoji = emoji_translate.get(body.reaction) or "❓"

        if body.reactor == self.steam.steam_id:
            if body.reaction_type == k_EMessageReactionType_Emoticon:
                contact_task = self.contacts.by_steam_id(
                    SteamID(msg.body.steamid_friend)
                )

                def callback(task: asyncio.Task[Contact]):
                    c = task.result()
                    if body.is_add:
                        c.user_reactions[timestamp].add(emoji)
                    else:
                        try:
                            c.user_reactions[timestamp].remove(emoji)
                        except KeyError:
                            self.log.warning(
                                "User removed a reaction we didn't know about"
                            )
                    c.carbon_react(timestamp, c.user_reactions[timestamp])

                contact_task.add_done_callback(callback)
        else:
            if body.reaction_type == k_EMessageReactionType_Emoticon:
                contact_task = self.contacts.by_steam_id(SteamID(msg.body.reactor))

                def callback(task: asyncio.Task[Contact]):
                    c = task.result()
                    if body.is_add:
                        c.contact_reactions[timestamp].add(emoji)
                    else:
                        try:
                            c.contact_reactions[timestamp].remove(emoji)
                        except KeyError:
                            self.log.warning(
                                "Contact removed a reaction we didn't know about"
                            )
                    c.update_reactions(timestamp)

                contact_task.add_done_callback(callback)

    def on_persona_state(self, msg: MsgProto):
        persona_state = msg.body
        self.log.debug("New state event: %s", persona_state)
        for f in persona_state.friends:
            if f.friendid == self.steam.steam_id:
                self.log.debug("This is me %s", self.steam.steam_id)
                return
            self.contacts.by_steam_id_apply(
                SteamID(f.friendid),
                partial(Contact.update_status, persona_state=f.persona_state),
            )

    async def logout(self):
        pass

    async def send_text(
        self,
        t: str,
        c: Contact,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text=None,
    ):
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
        old = c.user_reactions[legacy_msg_id]
        new = set[str]()
        for emoji in emojis:
            if emoji_translate.inverse.get(emoji) is None:
                self.send_gateway_message(
                    f"On steam, you can only react with {' '.join(emoji_translate.values())}"
                )
            else:
                new.add(emoji)

        for emoji_char in old - new:
            self.steam.send_um(
                "FriendMessages.UpdateMessageReaction#1",
                {
                    "steamid": SteamID(c.legacy_id).as_64,
                    "server_timestamp": legacy_msg_id,
                    "reaction_type": k_EMessageReactionType_Emoticon,
                    "reaction": emoji_translate.inverse.get(emoji_char),
                    "is_add": False,
                },
            )

        for emoji_char in new - old:
            self.steam.send_um(
                "FriendMessages.UpdateMessageReaction#1",
                {
                    "steamid": SteamID(c.legacy_id).as_64,
                    "server_timestamp": legacy_msg_id,
                    "reaction_type": k_EMessageReactionType_Emoticon,
                    "reaction": emoji_translate.inverse.get(emoji_char),
                    "is_add": True,
                },
            )

        c.user_reactions[legacy_msg_id] = new
        c.carbon_react(legacy_msg_id, new)

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
        (":steamthis:", "⬆"),
    ]
)
