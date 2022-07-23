import functools
import logging
import random
import shelve
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Hashable, Optional, Union

import aiohttp
import maufbapi.types.graphql
from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import Thread, ParticipantNode, Participant

from slidge import *
from slixmpp import Presence
from slixmpp.exceptions import XMPPError

from slidge.core.contact import LegacyContactType


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
    sent_messages: "Messages"
    received_messages: "Messages"

    contacts: Roster

    def post_init(self):
        self.shelf_path = self.xmpp.home_dir / self.user.bare_jid
        self.sent_messages = Messages()
        self.received_messages = Messages()

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
                # noinspection PyTypeCheckers
                self.api = api = AndroidAPI(state=s)
        self.send_gateway_message("Login successful")
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
        await self.mqtt.listen(self.mqtt.seq_id)

    async def add_friends(self):
        thread_list = await self.api.fetch_thread_list(msg_count=0)
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

    async def logout(self, p: Optional[Presence]):
        pass

    async def send_text(self, t: str, c: Contact) -> int:
        resp: mqtt_t.SendMessageResponse = await self.mqtt.send_message(
            target=c.legacy_id, message=t, is_group=False
        )
        timestamp = get_now_ms()
        log.debug("Send message response: %s", resp)
        if not resp.success:
            raise XMPPError(resp.error_message)
        self.sent_messages.add(c.legacy_id, timestamp)
        return timestamp

    async def send_file(self, u: str, c: Contact) -> int:
        pass

    async def active(self, c: Contact):
        pass

    async def inactive(self, c: Contact):
        pass

    async def composing(self, c: Contact):
        await self.mqtt.set_typing(target=c.legacy_id)

    async def paused(self, c: Contact):
        await self.mqtt.set_typing(target=c.legacy_id, typing=False)

    async def displayed(self, legacy_msg_id: Hashable, c: Contact):
        await self.mqtt.mark_read(
            target=c.legacy_id, read_to=get_now_ms(), is_group=False
        )

    async def on_fb_message(self, evt: Union[mqtt_t.Message, mqtt_t.ExtendedMessage]):
        meta = evt.metadata
        thread = (await self.api.fetch_thread_info(meta.thread.id))[0]
        if thread.is_group_thread:
            return
        contact = await self.contacts.from_thread(thread)

        if not contact.added_to_roster:
            await contact.add_to_roster()

        log.debug("Facebook message: %s", evt)
        if str(meta.sender) == self.me.id:
            if f"app_id:{self.mqtt.state.application.client_id}" in meta.tags:
                log.debug("Ignoring self message")
                return

            t = get_now_ms()
            contact.carbon(body=evt.text, legacy_id=t)
            self.sent_messages.add(contact.legacy_id, t)
        else:
            t = get_now_ms()
            self.received_messages.add(contact.legacy_id, t)
            contact.send_text(evt.text, legacy_msg_id=t)

    async def on_fb_message_read(self, receipt: mqtt_t.ReadReceipt):
        log.debug("Facebook read: %s", receipt)
        try:
            real_timestamp = self.sent_messages.find_closest(
                receipt.user_id, receipt.read_to
            )
        except KeyError:
            log.debug("Could not find message with corresponding timestamp, ignoring")
        else:
            self.contacts.by_legacy_id(receipt.user_id).displayed(real_timestamp)

    async def on_fb_typing(self, notification: mqtt_t.TypingNotification):
        c = self.contacts.by_legacy_id(notification.user_id)
        if notification.typing_status:
            c.composing()
        else:
            c.paused()

    async def on_fb_user_read(self, receipt: mqtt_t.OwnReadReceipt):
        when = receipt.read_to
        for thread in receipt.threads:
            c = self.contacts.by_legacy_id(thread.other_user_id)
            try:
                timestamp = self.received_messages.find_closest(c.legacy_id, when)
            except KeyError:
                log.debug("Cannot find message to carbon read, ignoring")
                continue
            c.carbon_read(timestamp)

    async def correct(self, text: str, legacy_msg_id: int, c: LegacyContactType):
        pass

    async def search(self, form_values: Dict[str, str]) -> SearchResult:
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


class Messages:
    MAX_LENGTH = 500

    def __init__(self):
        self._messages: Dict[int, Deque[int]] = defaultdict(
            functools.partial(deque, maxlen=self.MAX_LENGTH)
        )

    def add(self, contact_id: int, timestamp_ms: int):
        self._messages[contact_id].append(timestamp_ms)

    def find_closest(self, contact_id: int, approx_timestamp_ms: int) -> int:
        messages = self._messages[contact_id]
        t: Optional[int] = None
        while True:
            if len(messages) == 0:
                if t is None:
                    raise KeyError(contact_id, approx_timestamp_ms)
                else:
                    return t
            peek = messages[0]
            if peek < approx_timestamp_ms:
                t = messages.popleft()
            else:
                if t is None:
                    raise KeyError(contact_id, approx_timestamp_ms)
                return t


def get_now_ms():
    return time.time_ns() // 1_000_000


log = logging.getLogger(__name__)
