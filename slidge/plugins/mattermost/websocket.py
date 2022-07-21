import asyncio
import json
import logging
import pprint
import ssl
import time
from dataclasses import dataclass
from enum import Enum

import aiohttp


class EventType(str, Enum):
    AddedToTeam = "added_to_team"
    AuthenticationChallenge = "authentication_challenge"
    ChannelConverted = "channel_converted"
    ChannelCreated = "channel_created"
    ChannelDeleted = "channel_deleted"
    ChannelMemberUpdated = "channel_member_updated"
    ChannelUpdated = "channel_updated"
    ChannelViewed = "channel_viewed"
    ConfigChanged = "config_changed"
    DeleteTeam = "delete_team"
    DirectAdded = "direct_added"
    EmojiAdded = "emoji_added"
    EphemeralMessage = "ephemeral_message"
    GroupAdded = "group_added"
    Hello = "hello"
    LeaveTeam = "leave_team"
    LicenseChanged = "license_changed"
    MemberroleUpdated = "memberrole_updated"
    NewUser = "new_user"
    PluginDisabled = "plugin_disabled"
    PluginEnabled = "plugin_enabled"
    PluginStatusesChanged = "plugin_statuses_changed"
    PostDeleted = "post_deleted"
    PostEdited = "post_edited"
    PostUnread = "post_unread"
    Posted = "posted"
    PreferenceChanged = "preference_changed"
    PreferencesChanged = "preferences_changed"
    PreferencesDeleted = "preferences_deleted"
    ReactionAdded = "reaction_added"
    ReactionRemoved = "reaction_removed"
    Response = "response"
    RoleUpdated = "role_updated"
    StatusChange = "status_change"
    Typing = "typing"
    UpdateTeam = "update_team"
    UserAdded = "user_added"
    UserRemoved = "user_removed"
    UserRoleUpdated = "user_role_updated"
    UserUpdated = "user_updated"
    DialogOpened = "dialog_opened"
    ThreadUpdated = "thread_updated"
    ThreadFollowChanged = "thread_follow_changed"
    ThreadReadChanged = "thread_read_changed"

    # not in the https://api.mattermost.com
    SidebarCategoryUpdated = "sidebar_category_updated"

    Unknown = "__unknown__"


@dataclass
class MattermostEvent:
    type: EventType
    data: dict
    broadcast: dict
    left: dict

    def __str__(self):
        return (
            f"<{self.type}:"
            f" \ndata: {pprint.pformat(self.data)}"
            f" \nbroadcast: {pprint.pformat(self.broadcast)}"
            f" \nleft: {pprint.pformat(self.left)}"
            f">"
        )


class Websocket:
    def __init__(self, url, token):
        self.token = token
        self.url = url

        self._alive = False
        self._last_msg = 0

        self.ssl_verify = True
        self.keep_alive = True
        self.keep_alive_delay = 30

    async def connect(self, event_handler):
        """
        Connect to the websocket and authenticate it.
        When the authentication has finished, start the loop listening for messages,
        sending a ping to the server to keep the connection alive.
        :param event_handler: Every websocket event will be passed there. Takes one argument.
        :type event_handler: Function(message)
        :return:
        """
        context = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
        if not self.ssl_verify:
            context.verify_mode = ssl.CERT_NONE

        url = self.url
        self._alive = True

        while True:
            try:
                kw_args = {}
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        url,
                        ssl=context,
                        **kw_args,
                    ) as websocket:
                        await self._authenticate_websocket(websocket, event_handler)
                        while self._alive:
                            try:
                                await self._start_loop(websocket, event_handler)
                            except aiohttp.ClientError:
                                break
                        if (not self.keep_alive) or (not self._alive):
                            break
            except Exception as e:
                log.exception(
                    f"Failed to establish websocket connection: {type(e)} thrown"
                )
                await asyncio.sleep(self.keep_alive_delay)

    async def _start_loop(self, websocket, event_handler):
        """
        We will listen for websockets events, sending a heartbeats on a timer.
        If we don't the webserver would close the idle connection,
        forcing us to reconnect.
        """
        log.debug("Starting websocket loop")
        # TODO: move to create_task when cpython 3.7 is minimum supported python version
        keep_alive = asyncio.ensure_future(self._do_heartbeats(websocket))
        log.debug("Waiting for messages on websocket")
        while self._alive:
            message = await websocket.receive_str()
            self._last_msg = time.time()
            await handle_event(message, event_handler)
        log.debug("cancelling heartbeat task")
        keep_alive.cancel()
        try:
            await keep_alive
        except asyncio.CancelledError:
            pass

    async def _do_heartbeats(self, websocket):
        """
        This is a little complicated, but we only need to pong the websocket if
        we haven't received a message inside the timeout window.
        Since messages can be received, while we are waiting we need to check
        after sleep.
        """
        timeout = 30
        while True:
            since_last_msg = time.time() - self._last_msg
            next_timeout = (
                timeout - since_last_msg if since_last_msg <= timeout else timeout
            )
            await asyncio.sleep(next_timeout)
            if time.time() - self._last_msg >= timeout:
                log.debug("sending heartbeat...")
                await websocket.pong()
                self._last_msg = time.time()

    def disconnect(self):
        """Sets `self._alive` to False so the loop in `self._start_loop` will finish."""
        log.info("Disconnecting websocket")
        self._alive = False

    async def _authenticate_websocket(self, websocket, event_handler):
        """
        Sends an authentication challenge over a websocket.
        This is not needed when we just send the cookie we got on login
        when connecting to the websocket.
        """
        log.debug("Authenticating websocket")
        json_data = json.dumps(
            {
                "seq": 1,
                "action": "authentication_challenge",
                "data": {"token": self.token},
            }
        )
        await websocket.send_str(json_data)
        while True:
            message = await websocket.receive_str()
            status = json.loads(message)
            log.debug(status)
            # We want to pass the events to the event_handler already
            # because the hello event could arrive before the authentication ok response
            await handle_event(message, event_handler)
            if ("event" in status and status["event"] == "hello") and (
                "seq" in status and status["seq"] == 0
            ):
                log.info("Websocket authentification OK")
                return True
            log.error("Websocket authentification failed")


async def handle_event(message, event_handler):
    d = json.loads(message)
    if "event" in d:
        data = d.pop("data")
        try:
            event = EventType(d.pop("event"))
        except ValueError:
            event = EventType.Unknown
        bro = d.pop("broadcast")
        await event_handler(MattermostEvent(event, data, bro, d))


log = logging.getLogger(__name__)