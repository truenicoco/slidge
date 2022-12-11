import asyncio
import io
import pprint
import re
from datetime import datetime
from typing import Any, Optional

import emoji
from mattermost_api_reference_client.models import Status
from mattermost_api_reference_client.types import Unset
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *

from .api import ContactNotFound, MattermostClient
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
            required=False,
            type="boolean",
        ),
    ]

    ROSTER_GROUP = "Mattermost"

    COMPONENT_NAME = "Mattermost (slidge)"
    COMPONENT_TYPE = "mattermost"

    COMPONENT_AVATAR = "https://play-lh.googleusercontent.com/aX7JaAPkmnkeThK4kgb_HHlBnswXF0sPyNI8I8LNmEMMo1vDvMx32tCzgPMsyEXXzZRc"

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        mm_client = get_client_from_registration_form(registration_form)
        try:
            await mm_client.login()
        except Exception as e:
            raise ValueError("Could not authenticate: %s - %s", e, e.args)
        if mm_client.me is None:
            raise ValueError("Could not authenticate")


class Contact(LegacyContact["Session", str]):
    legacy_id: str

    MARKS = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._direct_channel_id: Optional[str] = None
        self._mm_id: Optional[str] = None

    def update_status(self, status: Optional[str]):
        if status is None:  # custom status
            self.session.log.debug("Status is None: %s", status)
            self.online()
        elif status == "online":
            self.online()
        elif status == "offline":
            self.offline()
        elif status == "away":
            self.away()
        elif status == "dnd":
            self.busy()
        else:
            self.session.log.warning(
                "Unknown status for '%s':",
                status,
            )

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
            [
                # TODO: find a better when than these non standard emoji aliases replace
                emoji.emojize(f":{x.replace('_3_', '_three_')}:", language="alias")
                for x in await self.session.get_mm_reactions(
                    legacy_msg_id, await self.mm_id()
                )
            ],
        )


class Roster(LegacyRoster["Session", Contact, str]):
    user_id_to_username: dict[str, str]
    direct_channel_id_to_username: dict[str, str]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.user_id_to_username = {}
        self.direct_channel_id_to_username = {}

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


class Session(
    BaseSession[
        Gateway, str, Roster, Contact, LegacyBookmarks, LegacyMUC, LegacyParticipant
    ]
):
    def __init__(self, user):
        super().__init__(user)
        self.messages_waiting_for_echo = set[str]()
        self.send_lock = asyncio.Lock()
        f = self.user.registration_form
        self.mm_client = get_client_from_registration_form(f)
        self.ws = Websocket(
            re.sub("^http", "ws", f["url"] or "")
            + (f["basepath"] or "")
            + (f["basepath_ws"] or ""),
            f["token"],
        )
        self.view_futures = dict[str, asyncio.Future[None]]()

    async def login(self):
        await self.mm_client.login()
        await self.add_contacts()
        self.xmpp.loop.create_task(self.ws.connect(self.on_mm_event))
        return f"Connected as '{(await self.mm_client.me).username}'"

    async def add_contacts(self):
        user_ids = await self.mm_client.get_contacts()
        contact_mm_users = await self.mm_client.get_users_by_ids(user_ids)
        contact_mm_statuses = await self.mm_client.get_users_statuses_by_ids(user_ids)

        statuses = {s.user_id: s for s in contact_mm_statuses}

        for user in contact_mm_users:
            status: Status = statuses[user.id]
            contact = await self.contacts.by_legacy_id(user.username)
            self.contacts.user_id_to_username[user.id] = user.username
            if user.nickname:
                contact.name = user.nickname
            elif user.first_name and user.last_name:
                contact.name = user.first_name + " " + user.last_name
            elif user.first_name:
                contact.name = user.first_name
            elif user.last_name:
                contact.name = user.last_name

            contact.avatar = await self.mm_client.get_profile_image(user.id)

            await contact.add_to_roster()
            contact.update_status(str(status.status))

    async def on_mm_event(self, event: MattermostEvent):
        self.log.debug("Event: %s", event)
        if event.type == EventType.Hello:
            self.log.debug("Received hello event: %s", event.data)
        elif event.type == EventType.Posted:
            post = event.data["post"]
            self.log.debug("Post: %s", pprint.pformat(post))

            text = post["message"]

            channel_id = post["channel_id"]
            post_id = post["id"]
            user_id = post["user_id"]

            if event.data["channel_type"] == "D":  # Direct messages?
                if user_id == await self.mm_client.mm_id:
                    try:
                        async with self.send_lock:
                            self.messages_waiting_for_echo.remove(post_id)
                    except KeyError:
                        members = await self.mm_client.get_channel_members(channel_id)
                        if len(members) > 2:
                            raise RuntimeError("Not a direct message after all")
                        for m in members:
                            if m.user_id != await self.mm_client.mm_id:
                                contact = await self.contacts.by_mm_user_id(m.user_id)
                                break
                        else:
                            raise RuntimeError("What?")

                        contact.send_text(
                            text,
                            legacy_msg_id=post_id,
                            when=datetime.fromtimestamp(post["update_at"] / 1000),
                            carbon=True,
                        )
                else:
                    contact = await self.contacts.by_mm_user_id(user_id)
                    if event.data.get("set_online"):
                        contact.online()
                    file_metas = post.get("metadata", {}).get("files", [])

                    if not file_metas:
                        contact.send_text(text, legacy_msg_id=post_id)
                        return

                    last_file_i = len(file_metas) - 1

                    for i, file_meta in enumerate(file_metas):
                        last = i == last_file_i
                        await contact.send_file(
                            filename=file_meta["name"],
                            input_file=io.BytesIO(
                                await self.mm_client.get_file(file_meta["id"])
                            ),
                            legacy_msg_id=post_id if last else None,
                            caption=text if last else None,
                        )
            elif event.data["channel_type"] == "P":
                # private channel
                pass
        elif event.type == EventType.ChannelViewed:
            channel_id = event.data["channel_id"]
            try:
                f = self.view_futures.pop(channel_id)
            except KeyError:
                pass
            else:
                f.set_result(None)
                return
            posts = await self.mm_client.get_posts_for_channel(channel_id)
            try:
                last_msg_id = posts.posts.additional_keys[-1]
            except IndexError:
                self.log.debug("ChannelViewed event for a channel with no messages")
                return
            if (c := await self.contacts.by_direct_channel_id(channel_id)) is None:
                self.log.debug("Ignoring unknown channel")
            else:
                c.displayed(last_msg_id, carbon=True)
        elif event.type == EventType.StatusChange:
            user_id = event.data["user_id"]
            if user_id == await self.mm_client.mm_id:
                self.log.debug("Own status change")
            else:

                contact = await self.contacts.by_mm_user_id(user_id)
                contact.update_status(event.data["status"])
        elif event.type == EventType.Typing:
            contact = await self.contacts.by_mm_user_id(event.data["user_id"])
            contact.composing()
        elif event.type == EventType.PostEdited:
            post = event.data["post"]
            if post["user_id"] == await self.mm_client.mm_id:
                if (
                    c := await self.contacts.by_direct_channel_id(post["channel_id"])
                ) is None:
                    self.log.debug("Ignoring edit in unknown channel")
                else:
                    c.correct(post["id"], post["message"], carbon=True)
            else:
                contact = await self.contacts.by_mm_user_id(post["user_id"])
                if post["channel_id"] == await contact.direct_channel_id():
                    contact.correct(post["id"], post["message"])
        elif event.type == EventType.PostDeleted:
            post = event.data["post"]
            if post["user_id"] == await self.mm_client.mm_id:
                if (
                    c := await self.contacts.by_direct_channel_id(post["channel_id"])
                ) is None:
                    self.log.debug("Ignoring edit in unknown channel")
                else:
                    c.retract(post["id"], carbon=True)
            else:
                contact = await self.contacts.by_mm_user_id(post["user_id"])
                if post["channel_id"] == await contact.direct_channel_id():
                    contact.retract(post["id"])
        elif event.type in (EventType.ReactionAdded, EventType.ReactionRemoved):
            reaction = event.data["reaction"]
            legacy_msg_id = reaction["post_id"]
            if (who := reaction["user_id"]) == await self.mm_client.mm_id:
                user_reactions_name = {
                    f":{x}:" for x in await self.get_mm_reactions(legacy_msg_id, who)
                }
                user_reactions_char = {
                    # TODO: find a better when than these non standard emoji aliases replace
                    emoji.emojize(x.replace("_3_", "_three_"), language="alias")
                    for x in user_reactions_name
                }
                self.log.debug(
                    "carbon: %s vs %s", user_reactions_name, user_reactions_char
                )
                contact = await self.contacts.by_direct_channel_id(
                    event.broadcast["channel_id"]
                )
                contact.react(legacy_msg_id, user_reactions_char, carbon=True)
            else:
                await (await self.contacts.by_mm_user_id(who)).update_reactions(
                    reaction["post_id"]
                )

    async def logout(self):
        pass

    async def send_text(self, text: str, chat: Contact, **k):
        async with self.send_lock:
            try:
                msg_id = await self.mm_client.send_message_to_user(chat.legacy_id, text)
            except ContactNotFound:
                raise XMPPError(
                    "recipient-unavailable", text="Cannot find this mattermost user"
                )

            self.messages_waiting_for_echo.add(msg_id)
            return msg_id

    async def send_file(self, url: str, chat: Contact, **k):
        channel_id = await chat.direct_channel_id()
        file_id = await self.mm_client.upload_file(channel_id, url)
        return await self.mm_client.send_message_with_file(channel_id, file_id)

    async def active(self, c: Contact):
        pass

    async def inactive(self, c: Contact):
        pass

    async def composing(self, c: Contact):
        await self.ws.user_typing(await c.direct_channel_id())

    async def paused(self, c: Contact):
        # no equivalent in MM, seems to have an automatic timeout in clients
        pass

    async def displayed(self, legacy_msg_id: Any, c: Contact):
        channel = await c.direct_channel_id()
        f = self.view_futures[channel] = self.xmpp.loop.create_future()
        await self.mm_client.view_channel(channel)
        await f

    async def correct(self, text: str, legacy_msg_id: Any, c: Contact):
        await self.mm_client.update_post(legacy_msg_id, text)

    async def search(self, form_values: dict[str, str]):
        pass

    async def retract(self, legacy_msg_id: Any, c: Contact):
        await self.mm_client.delete_post(legacy_msg_id)

    async def react(self, legacy_msg_id: Any, emojis: list[str], c: Contact):
        mm_reactions = await self.get_mm_reactions(
            legacy_msg_id, await self.mm_client.mm_id
        )
        xmpp_reactions = {
            emoji.demojize(x, language="alias", delimiters=("", "")) for x in emojis
        }
        self.log.debug("%s vs %s", mm_reactions, xmpp_reactions)
        for e in xmpp_reactions - mm_reactions:
            await self.mm_client.react(legacy_msg_id, e)
        for e in mm_reactions - xmpp_reactions:
            await self.mm_client.delete_reaction(legacy_msg_id, e)

    async def get_mm_reactions(self, legacy_msg_id: str, user_id: Optional[str]):
        return {
            x.emoji_name
            for x in await self.mm_client.get_reactions(legacy_msg_id)
            if x.user_id == user_id
        }


def get_client_from_registration_form(f: dict[str, Optional[str]]):
    url = (f.get("url") or "") + (f.get("basepath") or "")
    return MattermostClient(
        url,
        verify_ssl=f["strict_ssl"],
        timeout=5,
        token=f["token"],
    )
