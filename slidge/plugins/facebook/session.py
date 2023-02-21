import asyncio
import logging
import shelve
from collections import defaultdict
from typing import Union

import maufbapi.types
from maufbapi import AndroidAPI, AndroidState, ProxyHandler
from maufbapi.types import mqtt as mqtt_t
from maufbapi.types.graphql import Participant
from maufbapi.types.graphql.responses import FriendshipStatus

from slidge import *

from . import config
from .client import AndroidMQTT
from .contact import Contact, Roster
from .gateway import Gateway
from .util import FacebookMessage, Messages, get_shelf_path

Recipient = Union[Contact, LegacyMUC]


class Session(BaseSession[str, Recipient]):
    contacts: Roster

    mqtt: AndroidMQTT
    api: AndroidAPI
    me: maufbapi.types.graphql.OwnInfo
    my_id: int

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
        self.mqtt = AndroidMQTT(
            self, self.api.state, proxy_handler=self.api.proxy_handler
        )
        self.me = await self.api.get_self()
        self.my_id = int(self.me.id)  # bug in maufbapi? tulir said: "ask meta"
        await self.add_friends()
        self.mqtt.register_handlers()
        self.xmpp.loop.create_task(self.mqtt.listen(self.mqtt.seq_id))
        return f"Connected as '{self.me.name} <{self.me.email}>'"

    async def add_friends(self):
        thread_list = await self.api.fetch_thread_list(
            msg_count=0, thread_count=config.CHATS_TO_FETCH
        )
        self.mqtt.seq_id = int(thread_list.sync_sequence_id)
        self.log.debug("SEQ ID: %s", self.mqtt.seq_id)
        self.log.debug("Thread list: %s", thread_list)
        self.log.debug("Thread list page info: %s", thread_list.page_info)
        for t in thread_list.nodes:
            if t.is_group_thread:
                log.debug("Skipping group: %s", t)
                continue
            try:
                c = await self.contacts.by_thread(t)
            except XMPPError:
                self.log.warning("Something went wrong with this thread: %s", t)
                continue
            await c.add_to_roster()
            c.online()

    async def logout(self):
        pass

    async def send_text(
        self, chat: Recipient, text: str, *, reply_to_msg_id=None, **kwargs
    ) -> str:
        resp: mqtt_t.SendMessageResponse = await self.mqtt.send_message(
            target=chat.legacy_id,
            message=text,
            is_group=False,
            reply_to=reply_to_msg_id,
        )
        fut = self.ack_futures[
            resp.offline_threading_id
        ] = self.xmpp.loop.create_future()
        log.debug("Send message response: %s", resp)
        if not resp.success:
            raise XMPPError("internal-server-error", resp.error_message)
        fb_msg = await fut
        self.sent_messages[chat.legacy_id].add(fb_msg)
        return fb_msg.mid

    async def send_file(
        self, chat: Recipient, url: str, http_response, reply_to_msg_id=None, **_
    ):
        oti = self.mqtt.generate_offline_threading_id()
        fut = self.ack_futures[oti] = self.xmpp.loop.create_future()
        resp = await self.api.send_media(
            data=await http_response.read(),
            file_name=url.split("/")[-1],
            mimetype=http_response.content_type,
            offline_threading_id=oti,
            chat_id=chat.legacy_id,
            is_group=False,
            reply_to=reply_to_msg_id,
        )
        ack = await fut
        log.debug("Upload ack: %s", ack)
        return resp.media_id

    async def active(self, c: Recipient, thread=None):
        pass

    async def inactive(self, c: Recipient, thread=None):
        pass

    async def composing(self, c: Recipient, thread=None):
        await self.mqtt.set_typing(target=c.legacy_id)

    async def paused(self, c: Recipient, thread=None):
        await self.mqtt.set_typing(target=c.legacy_id, typing=False)

    async def displayed(self, c: Recipient, legacy_msg_id: str, thread=None):
        # fb_id = await c.fb_id()
        try:
            t = self.received_messages[c.legacy_id].by_mid[legacy_msg_id].timestamp_ms
        except KeyError:
            log.debug("Cannot find the timestamp of %s", legacy_msg_id)
        else:
            await self.mqtt.mark_read(target=c.legacy_id, read_to=t, is_group=False)

    async def correct(self, c: Recipient, text: str, legacy_msg_id: str, thread=None):
        pass

    async def react(
        self, c: Recipient, legacy_msg_id: str, emojis: list[str], thread=None
    ):
        # only reaction per msg on facebook, but this is handled by slidge core
        if len(emojis) == 0:
            emoji = None
        else:
            emoji = emojis[-1]
        f = self.reaction_futures[legacy_msg_id] = self.xmpp.loop.create_future()
        await self.api.react(legacy_msg_id, emoji)
        await f

    async def retract(self, c: Recipient, legacy_msg_id: str, thread=None):
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


log = logging.getLogger(__name__)
