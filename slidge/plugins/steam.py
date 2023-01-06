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
from typing import Any, Callable, Optional

import steam.enums
from slixmpp import JID
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
from slidge.core.adhoc import RegistrationType, TwoFactorNotRequired
from slidge.util import BiDict


class Gateway(BaseGateway["Session"]):
    REGISTRATION_INSTRUCTIONS = "Enter steam credentials"
    REGISTRATION_FIELDS = [
        FormField(var="username", label="Steam username", required=True),
        FormField(var="password", label="Password", private=True, required=True),
    ]
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE

    ROSTER_GROUP = "Steam"

    COMPONENT_NAME = "Steam (slidge)"
    COMPONENT_TYPE = "steam"

    COMPONENT_AVATAR = "https://logos-download.com/wp-content/uploads/2016/05/Steam_icon_logo_logotype.png"

    def __init__(self):
        super().__init__()
        self._pending_registrations = dict[str, tuple[SteamClient, EResult]]()
        # we store logged clients on registration to get it
        self.steam_clients = dict[str, SteamClient]()

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        username = registration_form["username"]
        password = registration_form["password"]

        store_dir = global_config.HOME_DIR / user_jid.bare
        store_dir.mkdir(exist_ok=True)

        client = SteamClient()
        client.set_credential_location(store_dir)

        login_result = client.login(username, password)
        if login_result == EResult.InvalidPassword:
            raise ValueError("Invalid password")
        elif login_result == EResult.OK:
            self.steam_clients[user_jid.bare] = client
            raise TwoFactorNotRequired
        elif login_result in (
            EResult.AccountLogonDenied,
            EResult.AccountLoginDeniedNeedTwoFactor,
        ):
            self._pending_registrations[user_jid.bare] = client, login_result
        else:
            raise ValueError(f"Login problem: {login_result}")

    async def validate_two_factor_code(self, user: GatewayUser, code: str):
        username = user.registration_form["username"]
        password = user.registration_form["password"]

        client, login_result = self._pending_registrations.pop(user.bare_jid)
        if login_result == EResult.AccountLogonDenied:
            # 2FA by mail (?)
            login_result = client.login(username, password, auth_code=code)
        elif login_result == EResult.AccountLoginDeniedNeedTwoFactor:
            # steam guard (?)
            login_result = client.login(username, password, two_factor_code=code)

        if login_result != EResult.OK:
            raise XMPPError(
                "forbidden", etype="auth", text=f"Could not login: {login_result}"
            )

        # store the client, so it's picked up on Sessions.login(), without re-auth
        self.steam_clients[user.bare_jid] = client


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

    async def available_emojis(self, legacy_msg_id):
        return set(emoji_translate.values())


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


class Session(
    BaseSession[
        Gateway, int, Roster, Contact, LegacyBookmarks, LegacyMUC, LegacyParticipant
    ]
):
    def __init__(self, user):
        super().__init__(user)
        store_dir = global_config.HOME_DIR / self.user.bare_jid
        store_dir.mkdir(exist_ok=True)

        self.job_futures = dict[str, asyncio.Future[Any]]()

        client = self.xmpp.steam_clients.pop(user.bare_jid, None)
        if client is None:
            self.steam = SteamClient()
            self.log.debug("Creating steam client, %s", store_dir)
            self.steam.set_credential_location(store_dir)
        else:
            # in case the session is created just after successful registration
            self.log.debug("Using found client: %s - %s", client, client.logged_on)
            self.steam = client

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(xmpp_msg_id: str):
        return int(xmpp_msg_id)

    async def login(self):
        self.steam.on(EMsg.ClientPersonaState, self.on_persona_state)
        self.steam.on("FriendMessagesClient.IncomingMessage#1", self.on_friend_message)
        self.steam.on("FriendMessagesClient.MessageReaction#1", self.on_friend_reaction)
        self.steam.on(EMsg.ServiceMethodResponse, self.on_service_method_response)

        if not self.steam.logged_on:
            # if just after registration, we're already logged on
            self.log.debug("Client is not logged on")
            login_result = self.steam.login(
                self.user.registration_form["username"],
                self.user.registration_form["password"],
            )
            self.log.debug("Re-login result: %s", login_result)

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
                    c.react(timestamp, c.user_reactions[timestamp], carbon=True)

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

    async def send_text(self, text: str, chat: Contact, **k):
        if not text:
            return
        job_id = self.steam.send_um(
            "FriendMessages.SendMessage#1",
            {
                "steamid": SteamID(chat.legacy_id),
                "chat_entry_type": steam.enums.EChatEntryType.ChatMsg,
                "message": text,
            },
        )
        f = self.job_futures[job_id] = self.xmpp.loop.create_future()
        return (await f).server_timestamp

    async def send_file(self, url: str, chat: Contact, **k):
        return await self.send_text(url, chat)

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
                # should not happen anymore, slidge core should  take care of that never happening
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
        c.react(legacy_msg_id, new, carbon=True)

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
