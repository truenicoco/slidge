import asyncio
import io
import logging
import random
import shelve
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path
from typing import Optional, Union

import aiohttp
import maufbapi.types.graphql
from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import Participant, ParticipantNode, Thread
from maufbapi.types.graphql.responses import FriendshipStatus
from slixmpp.exceptions import XMPPError

from slidge import *


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter facebook credentials"
    REGISTRATION_FIELDS = [
        FormField(var="email", label="Email", required=True),
        FormField(var="password", label="Password", required=True, private=True),
    ]

    ROSTER_GROUP = "Facebook"

    COMPONENT_NAME = "Facebook (slidge)"
    COMPONENT_TYPE = "facebook"
    COMPONENT_AVATAR = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Facebook_Messenger_logo_2018.svg/480px-Facebook_Messenger_logo_2018.svg.png"

    SEARCH_TITLE = "Search in your facebook friends"
    SEARCH_INSTRUCTIONS = "Enter something that can be used to search for one of your friends, eg, a first name"
    SEARCH_FIELDS = [FormField(var="query", label="Term(s)")]


class Contact(LegacyContact["Session"]):
    legacy_id: str  # facebook username, as in facebook.com/name.surname123

    def __init__(self, *a, **k):
        super(Contact, self).__init__(*a, **k)
        self._fb_id: Optional[int] = None

    async def fb_id(self):
        if self._fb_id is None:
            results = await self.session.api.search(
                self.legacy_id, entity_types=["user"]
            )
            for search_result in results.search_results.edges:
                result = search_result.node
                if (
                    isinstance(result, Participant)
                    and result.username == self.legacy_id
                ):
                    self._fb_id = int(result.id)
                    break
            else:
                raise XMPPError(
                    "not-found", text=f"Cannot find the facebook ID of {self.legacy_id}"
                )
            self.session.contacts.by_fb_id_dict[self._fb_id] = self
        return self._fb_id

    async def populate_from_participant(
        self, participant: ParticipantNode, update_avatar=True
    ):
        if self.legacy_id != participant.messaging_actor.username:
            raise RuntimeError(
                "Attempted to populate a contact with a non-corresponding participant"
            )
        self.name = participant.messaging_actor.name
        self._fb_id = int(participant.id)
        if self.avatar is None or update_avatar:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    participant.messaging_actor.profile_pic_large.uri
                ) as response:
                    response.raise_for_status()
                    self.avatar = await response.read()


class Roster(LegacyRoster[Contact, "Session"]):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.by_fb_id_dict: dict[int, Contact] = {}

    async def by_fb_id(self, fb_id: int) -> "Contact":
        contact = self.by_fb_id_dict.get(fb_id)
        if contact is None:
            thread = (await self.session.api.fetch_thread_info(fb_id))[0]
            return await self.by_thread(thread)
        return contact

    async def by_thread_key(self, t: mqtt_t.ThreadKey):
        if is_group_thread(t):
            raise ValueError("Thread seems to be a group thread")
        return await self.by_fb_id(t.other_user_id)

    async def by_thread(self, t: Thread):
        if t.is_group_thread:
            raise RuntimeError("Tried to populate a user from a group chat")

        if len(t.all_participants.nodes) != 2:
            raise RuntimeError(
                "Tried is not a group chat but doesn't have 2 participants ‽"
            )

        for participant in t.all_participants.nodes:
            if participant.id != self.session.me.id:
                break
        else:
            raise RuntimeError(
                "Couldn't find friend in thread participants", t.all_participants
            )

        contact = self.by_legacy_id(participant.messaging_actor.username)
        await contact.populate_from_participant(participant)
        self.by_fb_id_dict[int(participant.id)] = contact
        return contact


class Session(BaseSession[Contact, Roster, Gateway]):
    fb_state: AndroidState

    shelf_path: Path
    mqtt: AndroidMQTT
    api: AndroidAPI

    me: maufbapi.types.graphql.OwnInfo

    sent_messages: defaultdict[int, "Messages"]
    received_messages: defaultdict[int, "Messages"]
    # keys = "contact ID"

    ack_futures: dict[int, asyncio.Future["FacebookMessage"]]
    # keys = "offline thread ID"
    reaction_futures: dict[str, asyncio.Future[None]]
    unsend_futures: dict[str, asyncio.Future[None]]
    # keys = "facebook message id"

    contacts: Roster

    def post_init(self):
        self.shelf_path = self.xmpp.home_dir / self.user.bare_jid
        self.ack_futures = {}
        self.reaction_futures: dict[str, asyncio.Future] = {}
        self.unsend_futures: dict[str, asyncio.Future] = {}
        self.sent_messages = defaultdict(Messages)
        self.received_messages = defaultdict(Messages)

    async def login(self):
        shelf: shelve.Shelf[AndroidState]
        with shelve.open(str(self.shelf_path)) as shelf:
            try:
                self.fb_state = s = shelf["state"]
            except KeyError:
                s = AndroidState()
                self.api = api = AndroidAPI(state=s)
                s.generate(random.randbytes(30))  # type: ignore
                await api.mobile_config_sessionless()
                try:
                    login = await api.login(
                        email=self.user.registration_form["email"],
                        password=self.user.registration_form["password"],
                    )
                except maufbapi.http.errors.IncorrectPassword:
                    self.send_gateway_message("Incorrect password")
                    raise
                except maufbapi.http.errors.TwoFactorRequired:
                    code = await self.input(
                        "Reply to this message with your 2 factor authentication code"
                    )
                    login = await api.login_2fa(
                        email=self.user.registration_form["email"], code=code
                    )
                log.debug(login)
                self.fb_state = shelf["state"] = api.state
            else:
                self.api = api = AndroidAPI(state=s)
        self.mqtt = AndroidMQTT(api.state)
        self.me = await self.api.get_self()
        self.me.id = int(self.me.id)  # bug in maufbapi?
        await self.add_friends()
        self.mqtt.seq_id_update_callback = lambda i: setattr(self.mqtt, "seq_id", i)
        self.mqtt.add_event_handler(mqtt_t.Message, self.on_fb_message)
        self.mqtt.add_event_handler(mqtt_t.ExtendedMessage, self.on_fb_message)
        self.mqtt.add_event_handler(mqtt_t.ReadReceipt, self.on_fb_message_read)
        self.mqtt.add_event_handler(mqtt_t.TypingNotification, self.on_fb_typing)
        self.mqtt.add_event_handler(mqtt_t.OwnReadReceipt, self.on_fb_user_read)
        self.mqtt.add_event_handler(mqtt_t.Reaction, self.on_fb_reaction)
        self.mqtt.add_event_handler(mqtt_t.UnsendMessage, self.on_fb_unsend)

        self.mqtt.add_event_handler(mqtt_t.NameChange, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.AvatarChange, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.Presence, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.AddMember, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.RemoveMember, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.ThreadChange, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.MessageSyncError, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.ForcedFetch, self.on_fb_event)
        # self.mqtt.add_event_handler(Connect, self.on_connect)
        # self.mqtt.add_event_handler(Disconnect, self.on_disconnect)
        self.xmpp.loop.create_task(self.mqtt.listen(self.mqtt.seq_id))
        return f"Connected as '{self.me.name} <{self.me.email}>'"

    async def add_friends(self, n=2):
        thread_list = await self.api.fetch_thread_list(msg_count=0, thread_count=n)
        self.mqtt.seq_id = int(thread_list.sync_sequence_id)
        log.debug("SEQ ID: %s", self.mqtt.seq_id)
        self.log.debug("Thread list: %s", thread_list)
        self.log.debug("Thread list page info: %s", thread_list.page_info)
        for t in thread_list.nodes:
            if t.is_group_thread:
                log.debug("Skipping group: %s", t)
                continue
            c = await self.contacts.by_thread(t)
            await c.add_to_roster()
            c.online()

    async def logout(self):
        pass

    async def send_text(self, t: str, c: Contact, *, reply_to_msg_id=None) -> str:
        resp: mqtt_t.SendMessageResponse = await self.mqtt.send_message(
            target=(fb_id := await c.fb_id()),
            message=t,
            is_group=False,
            reply_to=reply_to_msg_id,
        )
        fut = self.ack_futures[
            resp.offline_threading_id
        ] = self.xmpp.loop.create_future()
        log.debug("Send message response: %s", resp)
        if not resp.success:
            raise XMPPError(resp.error_message)
        fb_msg = await fut
        self.sent_messages[fb_id].add(fb_msg)
        return fb_msg.mid

    async def send_file(self, u: str, c: Contact, *, reply_to_msg_id=None):
        async with aiohttp.ClientSession() as s:
            async with s.get(u) as r:
                data = await r.read()
        oti = self.mqtt.generate_offline_threading_id()
        fut = self.ack_futures[oti] = self.xmpp.loop.create_future()
        resp = await self.api.send_media(
            data=data,
            file_name=u.split("/")[-1],
            mimetype=guess_type(u)[0] or "application/octet-stream",
            offline_threading_id=oti,
            chat_id=await c.fb_id(),
            is_group=False,
            reply_to=reply_to_msg_id,
        )
        ack = await fut
        log.debug("Upload ack: %s", ack)
        return resp.media_id

    async def active(self, c: Contact):
        pass

    async def inactive(self, c: Contact):
        pass

    async def composing(self, c: Contact):
        await self.mqtt.set_typing(target=await c.fb_id())

    async def paused(self, c: Contact):
        await self.mqtt.set_typing(target=await c.fb_id(), typing=False)

    async def displayed(self, legacy_msg_id: str, c: Contact):
        fb_id = await c.fb_id()
        try:
            t = self.received_messages[fb_id].by_mid[legacy_msg_id].timestamp_ms
        except KeyError:
            log.debug("Cannot find the timestamp of %s", legacy_msg_id)
        else:
            await self.mqtt.mark_read(target=fb_id, read_to=t, is_group=False)

    async def on_fb_message(self, evt: Union[mqtt_t.Message, mqtt_t.ExtendedMessage]):
        if isinstance(evt, mqtt_t.ExtendedMessage):
            reply_to = evt.reply_to_message.metadata.id
            msg = evt.message
        else:
            reply_to = None
            msg = evt
        meta = msg.metadata
        if is_group_thread(thread_key := meta.thread):
            return
        contact = await self.contacts.by_thread_key(thread_key)

        if not contact.added_to_roster:
            await contact.add_to_roster()

        log.debug("Facebook message: %s", evt)
        fb_msg = FacebookMessage(mid=meta.id, timestamp_ms=meta.timestamp)
        if meta.sender == self.me.id:
            try:
                fut = self.ack_futures.pop(meta.offline_threading_id)
            except KeyError:
                log.debug("Received carbon %s - %s", meta.id, msg.text)
                contact.carbon(body=msg.text, legacy_id=meta.id)
                log.debug("Sent carbon")
                self.sent_messages[thread_key.other_user_id].add(fb_msg)
            else:
                log.debug("Received echo of %s", meta.offline_threading_id)
                fut.set_result(fb_msg)
        else:
            if msg.text:
                contact.send_text(
                    msg.text, legacy_msg_id=meta.id, reply_to_msg_id=reply_to
                )
            if msg.attachments:
                async with aiohttp.ClientSession() as c:
                    for a in msg.attachments:
                        url = (
                            ((v := a.video_info) and v.download_url)
                            or ((au := a.audio_info) and au.url)
                            or a.image_info.uri_map.get(0)
                        )
                        if url is None:
                            continue
                        async with c.get(url) as r:
                            await contact.send_file(
                                filename=a.file_name,
                                content_type=a.mime_type,
                                input_file=io.BytesIO(await r.read()),
                            )
            self.received_messages[thread_key.other_user_id].add(fb_msg)

    async def on_fb_message_read(self, receipt: mqtt_t.ReadReceipt):
        log.debug("Facebook read: %s", receipt)
        try:
            mid = self.sent_messages[receipt.user_id].pop_up_to(receipt.read_to).mid
        except KeyError:
            log.debug("Cannot find MID of %s", receipt.read_to)
        else:
            contact = await self.contacts.by_thread_key(receipt.thread)
            contact.displayed(mid)

    async def on_fb_typing(self, notification: mqtt_t.TypingNotification):
        log.debug("Facebook typing: %s", notification)
        c = await self.contacts.by_fb_id(notification.user_id)
        if notification.typing_status:
            c.composing()
        else:
            c.paused()

    async def on_fb_user_read(self, receipt: mqtt_t.OwnReadReceipt):
        log.debug("Facebook own read: %s", receipt)
        when = receipt.read_to
        for thread in receipt.threads:
            c = await self.contacts.by_fb_id(thread.other_user_id)
            try:
                mid = self.received_messages[await c.fb_id()].pop_up_to(when).mid
            except KeyError:
                log.debug("Cannot find mid of %s", when)
                continue
            c.carbon_read(mid)

    async def on_fb_reaction(self, reaction: mqtt_t.Reaction):
        self.log.debug("Reaction: %s", reaction)
        if is_group_thread(tk := reaction.thread):
            return
        contact = await self.contacts.by_thread_key(tk)
        mid = reaction.message_id
        if reaction.reaction_sender_id == self.me.id:
            try:
                f = self.reaction_futures.pop(mid)
            except KeyError:
                contact.carbon_react(mid, reaction.reaction or "")
            else:
                f.set_result(None)
        else:
            contact.react(reaction.message_id, reaction.reaction or "")

    async def on_fb_unsend(self, unsend: mqtt_t.UnsendMessage):
        self.log.debug("Unsend: %s", unsend)
        if is_group_thread(tk := unsend.thread):
            return
        contact = await self.contacts.by_thread_key(tk)
        mid = unsend.message_id
        if unsend.user_id == self.me.id:
            try:
                f = self.unsend_futures.pop(mid)
            except KeyError:
                contact.carbon_retract(mid)
            else:
                f.set_result(None)
        else:
            contact.retract(unsend.message_id)

    async def correct(self, text: str, legacy_msg_id: str, c: Contact):
        await self.api.unsend(legacy_msg_id)
        return await self.send_text(text, c)

    async def react(self, legacy_msg_id: str, emojis: list[str], c: Contact):
        if len(emojis) == 0:
            emoji = None
        else:
            emoji = emojis[-1]
            if len(emojis) > 1:  # only reaction per msg on facebook
                c.carbon_react(legacy_msg_id, emoji)
        f = self.reaction_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        await self.api.react(legacy_msg_id, emoji)
        await f

    async def retract(self, legacy_msg_id: str, c: Contact):
        f = self.unsend_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        await self.api.unsend(legacy_msg_id)
        await f

    async def search(self, form_values: dict[str, str]) -> SearchResult:
        results = await self.api.search(form_values["query"], entity_types=["user"])
        log.debug("Search results: %s", results)
        items = []
        for search_result in results.search_results.edges:
            result = search_result.node
            if isinstance(result, Participant):
                is_friend = (
                    friend := result.friendship_status
                ) is not None and friend == FriendshipStatus.ARE_FRIENDS
                items.append(
                    {
                        "name": result.name + " (friend)"
                        if is_friend
                        else " (not friend)",
                        "jid": f"{result.username}@{self.xmpp.boundjid.bare}",
                    }
                )

        return SearchResult(
            fields=[
                FormField(var="name", label="Name"),
                FormField(var="jid", label="JID", type="jid-single"),
            ],
            items=items,
        )

    @staticmethod
    async def on_fb_event(evt):
        log.debug("Facebook event: %s", evt)


@dataclass
class FacebookMessage:
    mid: str
    timestamp_ms: int


class Messages:
    def __init__(self):
        self.by_mid: OrderedDict[str, FacebookMessage] = OrderedDict()
        self.by_timestamp_ms: OrderedDict[int, FacebookMessage] = OrderedDict()

    def __len__(self):
        return len(self.by_mid)

    def add(self, m: FacebookMessage):
        self.by_mid[m.mid] = m
        self.by_timestamp_ms[m.timestamp_ms] = m

    def pop_up_to(self, approx_t: int) -> FacebookMessage:
        i = 0
        for i, t in enumerate(self.by_timestamp_ms.keys()):
            if t > approx_t:
                i -= 1
                break
        for j, t in enumerate(list(self.by_timestamp_ms.keys())):
            msg = self.by_timestamp_ms.pop(t)
            self.by_mid.pop(msg.mid)
            if j == i:
                return msg
        else:
            raise KeyError(approx_t)


def is_group_thread(t: mqtt_t.ThreadKey):
    return t.other_user_id is None and t.thread_fbid is not None


log = logging.getLogger(__name__)
