import asyncio
import json
import logging
import zlib
from collections import defaultdict
from typing import TYPE_CHECKING

import paho.mqtt.client as pmc
from maufbapi import AndroidMQTT as AndroidMQTTOriginal
from maufbapi.mqtt.otclient import MQTToTClient
from maufbapi.mqtt.subscription import RealtimeTopic
from maufbapi.thrift import ThriftObject
from maufbapi.types import mqtt as mqtt_t

from ... import XMPPError
from .util import FacebookMessage, is_group_thread

REQUEST_TIMEOUT = 60

if TYPE_CHECKING:
    from .session import Session


class AndroidMQTT(AndroidMQTTOriginal):
    def __init__(
        self,
        session: "Session",
        state,
        loop=None,
        log=None,
        connect_token_hash=None,
        proxy_handler=None,
    ):
        # overrides the default init to enable presences
        self.session = session
        self.seq_id = None
        self.seq_id_update_callback = None
        self.connect_token_hash = connect_token_hash
        self.region_hint_callback = None
        self.connection_unauthorized_callback = None
        self.enable_web_presence = True  # this is the modified line
        self._opened_thread = None
        self._publish_waiters = {}  # type:ignore
        self._response_waiters = {}  # type:ignore
        self._disconnect_error = None
        self._response_waiter_locks = defaultdict(lambda: asyncio.Lock())  # type:ignore
        self._event_handlers = defaultdict(lambda: [])  # type:ignore
        self._event_dispatcher_task = None
        self._outgoing_events = asyncio.Queue()  # type:ignore
        self.log = log or logging.getLogger("maufbapi.mqtt")
        self._loop = loop or asyncio.get_event_loop()
        self.state = state
        self._client = MQTToTClient(
            client_id=self._form_client_id(),
            clean_session=True,
            protocol=pmc.MQTTv31,
            transport="tcp",
        )
        self.proxy_handler = proxy_handler
        self.setup_proxy()
        self._client.enable_logger()
        self._client.tls_set()
        # mqtt.max_inflight_messages_set(20)  # The rest will get queued
        # mqtt.max_queued_messages_set(0)  # Unlimited messages can be queued
        # mqtt.message_retry_set(20)  # Retry sending for at least 20 seconds
        # mqtt.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.connect_async("edge-mqtt.facebook.com", 443, keepalive=60)
        self._client.on_message = self._on_message_handler
        self._client.on_publish = self._on_publish_handler
        self._client.on_connect = self._on_connect_handler
        self._client.on_disconnect = self._on_disconnect_handler
        self._client.on_socket_open = self._on_socket_open
        self._client.on_socket_close = self._on_socket_close
        self._client.on_socket_register_write = self._on_socket_register_write
        self._client.on_socket_unregister_write = self._on_socket_unregister_write

    def register_handlers(self):
        self.seq_id_update_callback = lambda i: setattr(  # type:ignore
            self, "seq_id", i
        )
        self.add_event_handler(mqtt_t.Message, self.on_fb_message)
        self.add_event_handler(mqtt_t.ExtendedMessage, self.on_fb_extended_message)
        self.add_event_handler(mqtt_t.ReadReceipt, self.on_fb_message_read)
        self.add_event_handler(mqtt_t.TypingNotification, self.on_fb_typing)
        self.add_event_handler(mqtt_t.OwnReadReceipt, self.on_fb_user_read)
        self.add_event_handler(mqtt_t.Reaction, self.on_fb_reaction)
        self.add_event_handler(mqtt_t.UnsendMessage, self.on_fb_unsend)
        self.add_event_handler(mqtt_t.Presence, self.on_fb_presence)

        self.add_event_handler(mqtt_t.NameChange, self.on_fb_event)
        self.add_event_handler(mqtt_t.AvatarChange, self.on_fb_event)
        self.add_event_handler(mqtt_t.AddMember, self.on_fb_event)
        self.add_event_handler(mqtt_t.RemoveMember, self.on_fb_event)
        self.add_event_handler(mqtt_t.ThreadChange, self.on_fb_event)
        self.add_event_handler(mqtt_t.MessageSyncError, self.on_fb_event)
        self.add_event_handler(mqtt_t.ForcedFetch, self.on_fb_event)

    # TODO: remove publish() and request() on maufbapi next release
    #       since our PR has been merged
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
            timeout_handle = self._loop.call_later(
                REQUEST_TIMEOUT, self._cancel_later, fut
            )
            fut.add_done_callback(lambda _: timeout_handle.cancel())
            return await fut

    async def _dispatch(self, evt) -> None:
        # by default, AndroidMQTT logs any exceptions here, but we actually
        # want to let it propagate
        for handler in self._event_handlers[type(evt)]:
            self.log.trace("Dispatching event %s", evt)  # type:ignore
            await handler(evt)

    async def on_fb_extended_message(self, evt: mqtt_t.ExtendedMessage):
        log.debug("Extended message")
        kwargs = {}
        msg = evt.message

        if reply_to_fb_msg := evt.reply_to_message:
            log.debug("Reply-to")
            kwargs["reply_to_msg_id"] = reply_to_fb_msg.metadata.id
            kwargs["reply_to_fallback_text"] = reply_to_fb_msg.text
            kwargs["reply_self"] = (
                reply_to_fb_msg.metadata.sender == msg.metadata.sender
            )
        await self.on_fb_message(msg, **kwargs)

    async def on_fb_message(self, msg: mqtt_t.Message, **kwargs):
        meta = msg.metadata
        if is_group_thread(thread_key := meta.thread):
            return

        contact = await self.session.contacts.by_thread_key(thread_key)

        fb_msg = FacebookMessage(mid=meta.id, timestamp_ms=meta.timestamp)

        if not contact.added_to_roster:
            await contact.add_to_roster()

        if meta.sender == self.session.my_id:
            try:
                fut = self.session.ack_futures.pop(meta.offline_threading_id)
            except KeyError:
                log.debug("Received carbon %s - %s", meta.id, msg.text)
                kwargs["carbon"] = True
                log.debug("Sent carbon")
                self.session.sent_messages[thread_key.other_user_id].add(fb_msg)
            else:
                log.debug("Received echo of %s", meta.offline_threading_id)
                fut.set_result(fb_msg)
                return
        else:
            self.session.received_messages[thread_key.other_user_id].add(fb_msg)

        await contact.send_fb_message(msg, **kwargs)

    async def on_fb_message_read(self, receipt: mqtt_t.ReadReceipt):
        log.debug("Facebook read: %s", receipt)
        try:
            mid = (
                self.session.sent_messages[receipt.user_id]
                .pop_up_to(receipt.read_to)
                .mid
            )
        except KeyError:
            log.debug("Cannot find MID of %s", receipt.read_to)
        else:
            contact = await self.session.contacts.by_thread_key(receipt.thread)
            contact.displayed(mid)

    async def on_fb_typing(self, notification: mqtt_t.TypingNotification):
        log.debug("Facebook typing: %s", notification)
        c = await self.session.contacts.by_legacy_id(notification.user_id)
        if notification.typing_status:
            c.composing()
        else:
            c.paused()

    async def on_fb_user_read(self, receipt: mqtt_t.OwnReadReceipt):
        log.debug("Facebook own read: %s", receipt)
        when = receipt.read_to
        for thread in receipt.threads:
            c = await self.session.contacts.by_legacy_id(thread.other_user_id)
            try:
                mid = self.session.received_messages[c.legacy_id].pop_up_to(when).mid
            except KeyError:
                log.debug("Cannot find mid of %s", when)
                continue
            c.displayed(mid, carbon=True)

    async def on_fb_reaction(self, reaction: mqtt_t.Reaction):
        self.log.debug("Reaction: %s", reaction)
        if is_group_thread(tk := reaction.thread):
            return
        contact = await self.session.contacts.by_thread_key(tk)
        mid = reaction.message_id
        if reaction.reaction_sender_id == self.session.my_id:
            try:
                f = self.session.reaction_futures.pop(mid)
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
        contact = await self.session.contacts.by_thread_key(tk)
        mid = unsend.message_id
        if unsend.user_id == self.session.my_id:
            try:
                f = self.session.unsend_futures.pop(mid)
            except KeyError:
                contact.retract(mid, carbon=True)
            else:
                f.set_result(None)
        else:
            contact.retract(unsend.message_id)

    async def on_fb_presence(self, presence: mqtt_t.Presence):
        for info in presence.updates:
            try:
                contact = await self.session.contacts.by_legacy_id(info.user_id)
            except XMPPError:
                continue
            if not contact.added_to_roster:
                await contact.add_to_roster()
            contact.update_presence(info)

    @staticmethod
    async def on_fb_event(evt):
        log.debug("Facebook event: %s", evt)


log = logging.getLogger(__name__)
