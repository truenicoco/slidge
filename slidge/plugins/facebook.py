import asyncio
import io
import logging
import random
import shelve
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path
from typing import Union

import aiohttp
import maufbapi.types.graphql
from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import Participant, ParticipantNode, Thread
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
    legacy_id: int

    async def populate_from_participant(
        self, participant: ParticipantNode, update_avatar=True
    ):
        if self.legacy_id != int(participant.id):
            raise RuntimeError(
                "Attempted to populate a contact with a non-corresponding participant"
            )
        self.name = participant.messaging_actor.name
        if self.avatar is None or update_avatar:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    participant.messaging_actor.profile_pic_large.uri
                ) as response:
                    response.raise_for_status()
                    self.avatar = await response.read()


class Roster(LegacyRoster[Contact, "Session"]):
    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> int:
        return int(jid_username)

    async def from_thread(self, t: Thread):
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

        contact = self.by_legacy_id(int(participant.id))
        await contact.populate_from_participant(participant)
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

    contacts: Roster

    def post_init(self):
        self.shelf_path = self.xmpp.home_dir / self.user.bare_jid
        self.ack_futures = {}
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
        await self.add_friends()
        self.mqtt.seq_id_update_callback = lambda i: setattr(self.mqtt, "seq_id", i)
        self.mqtt.add_event_handler(mqtt_t.Message, self.on_fb_message)
        self.mqtt.add_event_handler(mqtt_t.ExtendedMessage, self.on_fb_message)
        self.mqtt.add_event_handler(mqtt_t.ReadReceipt, self.on_fb_message_read)
        self.mqtt.add_event_handler(mqtt_t.TypingNotification, self.on_fb_typing)
        self.mqtt.add_event_handler(mqtt_t.OwnReadReceipt, self.on_fb_user_read)

        self.mqtt.add_event_handler(mqtt_t.NameChange, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.AvatarChange, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.UnsendMessage, self.on_fb_event)
        self.mqtt.add_event_handler(mqtt_t.Reaction, self.on_fb_event)
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
            c = await self.contacts.from_thread(t)
            await c.add_to_roster()
            c.online()

    async def logout(self):
        pass

    async def send_text(self, t: str, c: Contact) -> str:
        resp: mqtt_t.SendMessageResponse = await self.mqtt.send_message(
            target=c.legacy_id, message=t, is_group=False
        )
        self.ack_futures[resp.offline_threading_id] = self.xmpp.loop.create_future()
        log.debug("Send message response: %s", resp)
        if not resp.success:
            raise XMPPError(resp.error_message)
        fb_msg = await self.ack_futures[resp.offline_threading_id]
        self.sent_messages[c.legacy_id].add(fb_msg)
        return fb_msg.mid

    async def send_file(self, u: str, c: Contact):
        async with aiohttp.ClientSession() as s:
            async with s.get(u) as r:
                data = await r.read()
        oti = self.mqtt.generate_offline_threading_id()
        self.ack_futures[oti] = self.xmpp.loop.create_future()
        resp = await self.api.send_media(
            data=data,
            file_name=u.split("/")[-1],
            mimetype=guess_type(u)[0] or "application/octet-stream",
            offline_threading_id=oti,
            chat_id=c.legacy_id,
            is_group=False,
        )
        ack = await self.ack_futures[oti]
        log.debug("Upload ack: %s", ack)
        return resp.media_id

    async def active(self, c: Contact):
        pass

    async def inactive(self, c: Contact):
        pass

    async def composing(self, c: Contact):
        await self.mqtt.set_typing(target=c.legacy_id)

    async def paused(self, c: Contact):
        await self.mqtt.set_typing(target=c.legacy_id, typing=False)

    async def displayed(self, legacy_msg_id: str, c: Contact):
        try:
            t = self.sent_messages[c.legacy_id].by_mid[legacy_msg_id].timestamp_ms
        except KeyError:
            log.debug("Cannot find the timestamp of %s", legacy_msg_id)
        else:
            await self.mqtt.mark_read(target=c.legacy_id, read_to=t, is_group=False)

    async def on_fb_message(self, evt: Union[mqtt_t.Message, mqtt_t.ExtendedMessage]):
        meta = evt.metadata
        thread = (await self.api.fetch_thread_info(meta.thread.id))[0]
        if thread.is_group_thread:
            return
        contact = await self.contacts.from_thread(thread)

        if not contact.added_to_roster:
            await contact.add_to_roster()

        log.debug("Facebook message: %s", evt)
        fb_msg = FacebookMessage(mid=meta.id, timestamp_ms=meta.timestamp)
        if str(meta.sender) == self.me.id:
            try:
                fut = self.ack_futures.pop(meta.offline_threading_id)
            except KeyError:
                log.debug("Received carbon %s - %s", meta.id, evt.text)
                contact.carbon(body=evt.text, legacy_id=meta.id)
                log.debug("Sent carbon")
                self.sent_messages[contact.legacy_id].add(fb_msg)
            else:
                log.debug("Received echo of %s", meta.offline_threading_id)
                fut.set_result(fb_msg)
        else:
            contact.send_text(evt.text, legacy_msg_id=meta.id)
            if evt.attachments:
                async with aiohttp.ClientSession() as c:
                    for a in evt.attachments:
                        url = (
                            a.image_info.uri_map.get(0)
                            or a.audio_info.url
                            or a.video_info.download_url
                        )
                        if url is None:
                            continue
                        async with c.get(url) as r:
                            await contact.send_file(
                                filename=a.file_name,
                                content_type=a.mime_type,
                                input_file=io.BytesIO(await r.read()),
                            )
            self.received_messages[contact.legacy_id].add(fb_msg)

    async def on_fb_message_read(self, receipt: mqtt_t.ReadReceipt):
        log.debug("Facebook read: %s", receipt)
        try:
            mid = self.sent_messages[receipt.user_id].pop_up_to(receipt.read_to).mid
        except KeyError:
            log.debug("Cannot find MID of %s", receipt.read_to)
        else:
            self.contacts.by_legacy_id(receipt.user_id).displayed(mid)

    async def on_fb_typing(self, notification: mqtt_t.TypingNotification):
        log.debug("Facebook typing: %s", notification)
        c = self.contacts.by_legacy_id(notification.user_id)
        if notification.typing_status:
            c.composing()
        else:
            c.paused()

    async def on_fb_user_read(self, receipt: mqtt_t.OwnReadReceipt):
        log.debug("Facebook own read: %s", receipt)
        when = receipt.read_to
        for thread in receipt.threads:
            c = self.contacts.by_legacy_id(thread.other_user_id)
            try:
                mid = self.received_messages[c.legacy_id].pop_up_to(when).mid
            except KeyError:
                log.debug("Cannot find mid of %s", when)
                continue
            c.carbon_read(mid)

    async def correct(self, text: str, legacy_msg_id: str, c: Contact):
        await self.api.unsend(legacy_msg_id)
        return await self.send_text(text, c)

    async def search(self, form_values: dict[str, str]) -> SearchResult:
        results = await self.api.search(form_values["query"], entity_types=["user"])
        log.debug("Search results: %s", results)
        items = []
        for search_result in results.search_results.edges:
            result = search_result.node
            if isinstance(result, Participant):
                items.append(
                    {
                        "name": result.name,
                        "jid": f"{result.id}@{self.xmpp.boundjid.bare}",
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


log = logging.getLogger(__name__)
