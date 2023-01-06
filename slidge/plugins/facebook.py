import asyncio
import io
import json
import logging
import random
import shelve
import zlib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from mimetypes import guess_type
from typing import Optional, Union

import aiohttp
import maufbapi.types.graphql
from maufbapi import AndroidAPI, AndroidMQTT, AndroidState
from maufbapi.mqtt.subscription import RealtimeTopic
from maufbapi.proxy import ProxyHandler
from maufbapi.thrift import ThriftObject
from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import Participant, ParticipantNode, Thread
from maufbapi.types.graphql.responses import FriendshipStatus
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *
from slidge.core.adhoc import RegistrationType, TwoFactorNotRequired


class Config:
    CHATS_TO_FETCH = 20
    CHATS_TO_FETCH__DOC = (
        "The number of most recent chats to fetch on startup. "
        "Getting all chats might hit rate limiting and possibly account lock. "
        "Please report if you try with high values and don't hit any problem!"
    )


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter facebook credentials"
    REGISTRATION_FIELDS = [
        FormField(var="email", label="Email", required=True),
        FormField(var="password", label="Password", required=True, private=True),
    ]
    REGISTRATION_MULTISTEP = True
    REGISTRATION_TYPE = RegistrationType.TWO_FACTOR_CODE

    ROSTER_GROUP = "Facebook"

    COMPONENT_NAME = "Facebook (slidge)"
    COMPONENT_TYPE = "facebook"
    COMPONENT_AVATAR = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Facebook_Messenger_logo_2018.svg/480px-Facebook_Messenger_logo_2018.svg.png"

    SEARCH_TITLE = "Search in your facebook friends"
    SEARCH_INSTRUCTIONS = "Enter something that can be used to search for one of your friends, eg, a first name"
    SEARCH_FIELDS = [FormField(var="query", label="Term(s)")]

    def __init__(self):
        super().__init__()
        self._pending_reg = dict[str, AndroidAPI]()

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        s = AndroidState()
        x = ProxyHandler(None)
        api = AndroidAPI(state=s, proxy_handler=x)
        s.generate(random.randbytes(30))  # type: ignore
        await api.mobile_config_sessionless()
        try:
            await api.login(
                email=registration_form["email"], password=registration_form["password"]
            )
        except maufbapi.http.errors.TwoFactorRequired:
            self._pending_reg[user_jid.bare] = api
        except maufbapi.http.errors.OAuthException as e:
            raise XMPPError("not-authorized", text=str(e))
        else:
            save_state(user_jid.bare, api.state)
            raise TwoFactorNotRequired

    async def validate_two_factor_code(self, user: GatewayUser, code):
        api = self._pending_reg.pop(user.bare_jid)
        try:
            await api.login_2fa(email=user.registration_form["email"], code=code)
        except maufbapi.http.errors as e:
            raise XMPPError("not-authorized", text=str(e))
        save_state(user.bare_jid, api.state)


def get_shelf_path(user_bare_jid):
    return str(global_config.HOME_DIR / user_bare_jid)


def save_state(user_bare_jid: str, state: AndroidState):
    shelf_path = get_shelf_path(user_bare_jid)
    with shelve.open(shelf_path) as shelf:
        shelf["state"] = state


class Contact(LegacyContact["Session", str]):
    # legacy_id = facebook username, as in facebook.com/name.surname123
    REACTIONS_SINGLE_EMOJI = True

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
            self.avatar = participant.messaging_actor.profile_pic_large.uri


class Roster(LegacyRoster["Session", Contact, str]):
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

        contact = await self.by_legacy_id(participant.messaging_actor.username)
        await contact.populate_from_participant(participant)
        self.by_fb_id_dict[int(participant.id)] = contact
        return contact


class Session(
    BaseSession[
        Gateway, str, Roster, Contact, LegacyBookmarks, LegacyMUC, LegacyParticipant
    ]
):
    mqtt: AndroidMQTT
    api: AndroidAPI
    me: maufbapi.types.graphql.OwnInfo

    def __init__(self, user):
        super().__init__(user)

        # keys = "offline thread ID"
        self.ack_futures = dict[int, asyncio.Future[FacebookMessage]]()

        # keys = "facebook message id"
        self.reaction_futures = dict[str, asyncio.Future]()
        self.unsend_futures = dict[str, asyncio.Future]()

        # keys = "contact ID"
        self.sent_messages = defaultdict[int, Messages](Messages)
        self.received_messages = defaultdict[int, Messages](Messages)

    async def login(self):
        shelf: shelve.Shelf[AndroidState]
        with shelve.open(get_shelf_path(self.user.bare_jid)) as shelf:
            s = shelf["state"]
        x = ProxyHandler(None)
        self.api = AndroidAPI(state=s, proxy_handler=x)
        self.mqtt = AndroidMQTT(self.api.state, proxy_handler=self.api.proxy_handler)
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

    async def add_friends(self):
        thread_list = await self.api.fetch_thread_list(
            msg_count=0, thread_count=Config.CHATS_TO_FETCH
        )
        self.mqtt.seq_id = int(thread_list.sync_sequence_id)
        self.log.debug("SEQ ID: %s", self.mqtt.seq_id)
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

    async def send_text(
        self, text: str, chat: Contact, *, reply_to_msg_id=None, **kwargs
    ) -> str:
        resp: mqtt_t.SendMessageResponse = await self.mqtt.send_message(
            target=(fb_id := await chat.fb_id()),
            message=text,
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

    async def send_file(self, url: str, chat: Contact, reply_to_msg_id=None, **kwargs):
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                data = await r.read()
        oti = self.mqtt.generate_offline_threading_id()
        fut = self.ack_futures[oti] = self.xmpp.loop.create_future()
        resp = await self.api.send_media(
            data=data,
            file_name=url.split("/")[-1],
            mimetype=guess_type(url)[0] or "application/octet-stream",
            offline_threading_id=oti,
            chat_id=await chat.fb_id(),
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
                contact.send_text(body=msg.text, legacy_id=meta.id, carbon=True)
                log.debug("Sent carbon")
                self.sent_messages[thread_key.other_user_id].add(fb_msg)
            else:
                log.debug("Received echo of %s", meta.offline_threading_id)
                fut.set_result(fb_msg)
        else:
            self.received_messages[thread_key.other_user_id].add(fb_msg)

            text = msg.text
            msg_id = meta.id
            if not (attachments := msg.attachments):
                if text:
                    contact.send_text(
                        text, legacy_msg_id=msg_id, reply_to_msg_id=reply_to
                    )
                return

            last_attachment_i = len(attachments) - 1
            async with aiohttp.ClientSession() as c:
                for i, a in enumerate(attachments):
                    last = i == last_attachment_i
                    try:
                        url = (
                            ((v := a.video_info) and v.download_url)
                            or ((au := a.audio_info) and au.url)
                            or a.image_info.uri_map.get(0)
                        )
                    except AttributeError:
                        log.warning("Unhandled attachment: %s", a)
                        contact.send_text(
                            "/me sent an attachment that slidge does not support"
                        )
                        continue
                    if url is None:
                        if last:
                            contact.send_text(
                                text, legacy_msg_id=msg_id, reply_to_msg_id=reply_to
                            )
                        continue
                    async with c.get(url) as r:
                        await contact.send_file(
                            filename=a.file_name,
                            content_type=a.mime_type,
                            input_file=io.BytesIO(await r.read()),
                            caption=text if last else None,
                            legacy_msg_id=msg_id if last else None,
                            reply_to_msg_id=reply_to if last else None,
                        )

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
            c.displayed(mid, carbon=True)

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
                contact.react(mid, reaction.reaction or "", carbon=True)
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
                contact.retract(mid, carbon=True)
            else:
                f.set_result(None)
        else:
            contact.retract(unsend.message_id)

    async def correct(self, text: str, legacy_msg_id: str, c: Contact):
        await self.api.unsend(legacy_msg_id)
        return await self.send_text(text, c)

    async def react(self, legacy_msg_id: str, emojis: list[str], c: Contact):
        # only reaction per msg on facebook, but this is handled by slidge core
        if len(emojis) == 0:
            emoji = None
        else:
            emoji = emojis[-1]
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


# Monkeypatch
# TODO: remove me when https://github.com/mautrix/facebook/pull/270 is merged
# and a new maufbapi is released


REQUEST_TIMEOUT = 60


def publish(
    self,
    topic,
    payload,
    prefix: bytes = b"",
    compress: bool = True,
) -> asyncio.Future:
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if isinstance(payload, ThriftObject):
        payload = payload.to_thrift()
    if compress:
        payload = zlib.compress(prefix + payload, level=9)
    elif prefix:
        payload = prefix + payload
    info = self._client.publish(
        topic.encoded if isinstance(topic, RealtimeTopic) else topic, payload, qos=1
    )
    fut = self._loop.create_future()
    timeout_handle = self._loop.call_later(REQUEST_TIMEOUT, self._cancel_later, fut)
    fut.add_done_callback(lambda _: timeout_handle.cancel())
    self._publish_waiters[info.mid] = fut
    return fut


async def request(
    self,
    topic: RealtimeTopic,
    response: RealtimeTopic,
    payload,
    prefix: bytes = b"",
):
    async with self._response_waiter_locks[response]:
        fut = self._loop.create_future()
        self._response_waiters[response] = fut
        await self.publish(topic, payload, prefix)
        timeout_handle = self._loop.call_later(REQUEST_TIMEOUT, self._cancel_later, fut)
        fut.add_done_callback(lambda _: timeout_handle.cancel())
        return await fut


AndroidMQTT.publish = publish
AndroidMQTT.request = request


log = logging.getLogger(__name__)
