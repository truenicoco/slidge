import asyncio
import re
from typing import TYPE_CHECKING, Any, Optional, Union

from mattermost_api_reference_client.models import Post, Reaction
from mattermost_api_reference_client.models.user import User

from slidge import *

from .api import ContactNotFound
from .util import get_client_from_registration_form
from .websocket import MattermostEvent, Websocket

if TYPE_CHECKING:
    from .contact import Contact, Roster
    from .gateway import Gateway


Recipient = Union["Contact", "LegacyMUC"]


class Session(BaseSession[str, Recipient]):
    contacts: "Roster"
    MESSAGE_IDS_ARE_THREAD_IDS = True

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
        self.xmpp.loop.create_task(self.ws.connect(self.on_mm_event))
        self.xmpp.loop.create_task(self.contacts.update_statuses())
        return f"Connected as '{(await self.mm_client.me).username}'"

    async def on_mm_event(self, event: MattermostEvent):
        self.log.debug("Event: %s", event)
        handler = getattr(self, f"on_mm_{event.type.name}", None)
        if handler:
            return await handler(event)

        self.log.debug("Ignored event: %s", event.type)

    async def on_mm_Posted(self, event: MattermostEvent):
        post = get_post_from_dict(event.data["post"])
        self.log.debug("Post: %s", post)
        user_id = post.user_id
        assert isinstance(user_id, str)
        assert isinstance(post.id, str)

        if event.data["channel_type"] == "D":  # Direct messages
            carbon = post.user_id == await self.mm_client.mm_id
            if carbon:
                try:
                    async with self.send_lock:
                        self.messages_waiting_for_echo.remove(post.id)
                except KeyError:
                    contact = await self.contacts.by_direct_channel_id(post.channel_id)
                else:
                    return
            else:
                contact = await self.contacts.by_mm_user_id(user_id)
            if not carbon and event.data.get("set_online"):
                contact.update_status()
            await contact.send_mm_post(post, carbon)

        elif event.data["channel_type"] == "P":
            # private channel
            pass

    async def on_mm_ChannelViewed(self, event: MattermostEvent):
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

    async def on_mm_StatusChange(self, event: MattermostEvent):
        user_id = event.data["user_id"]
        if user_id == await self.mm_client.mm_id:
            self.log.debug("Own status change")
        else:
            contact = await self.contacts.by_mm_user_id(user_id)
            contact.update_status(event.data["status"])

    async def on_mm_Typing(self, event: MattermostEvent):
        contact = await self.contacts.by_mm_user_id(event.data["user_id"])
        if event.broadcast["channel_id"] == await contact.direct_channel_id():
            contact.composing()

    async def on_mm_PostEdited(self, event: MattermostEvent):
        post = get_post_from_dict(event.data["post"])
        if post.user_id == await self.mm_client.mm_id:
            if (c := await self.contacts.by_direct_channel_id(post.channel_id)) is None:
                self.log.debug("Ignoring edit in unknown channel")
            else:
                c.correct(post.id, post.message, carbon=True)
        else:
            contact = await self.contacts.by_mm_user_id(post.user_id)
            if post.channel_id == await contact.direct_channel_id():
                contact.correct(post.id, post.message)

    async def on_mm_PostDeleted(self, event: MattermostEvent):
        post = get_post_from_dict(event.data["post"])
        if post.user_id == await self.mm_client.mm_id:
            if (c := await self.contacts.by_direct_channel_id(post.channel_id)) is None:
                self.log.debug("Ignoring edit in unknown channel")
            else:
                c.retract(post.id, carbon=True)
        else:
            contact = await self.contacts.by_mm_user_id(post.user_id)
            if post.channel_id == await contact.direct_channel_id():
                contact.retract(post.id)

    async def on_mm_ReactionAdded(self, event: MattermostEvent):
        await self.on_mm_reaction(event)

    async def on_mm_ReactionRemoved(self, event: MattermostEvent):
        await self.on_mm_reaction(event)

    async def on_mm_reaction(self, event: MattermostEvent):
        reaction = get_reaction_from_dict(event.data["reaction"])
        legacy_msg_id = reaction.post_id
        if (who := reaction.user_id) == await self.mm_client.mm_id:
            contact = await self.contacts.by_direct_channel_id(
                event.broadcast["channel_id"]
            )
            contact.react(
                legacy_msg_id,
                await self.get_mm_reactions(legacy_msg_id, who),
                carbon=True,
            )
        else:
            await (await self.contacts.by_mm_user_id(who)).update_reactions(
                reaction.post_id
            )

    async def on_mm_UserUpdated(self, event: MattermostEvent):
        user = User.from_dict(event.data["user"])
        assert isinstance(user.username, str)
        c = await self.contacts.by_legacy_id(user.username)
        await c.update_info(user)

    async def logout(self):
        pass

    async def send_text(self, chat: Recipient, text: str, thread=None, **k):
        async with self.send_lock:
            try:
                msg_id = await self.mm_client.send_message_to_user(
                    chat.legacy_id, text, thread
                )
            except ContactNotFound:
                raise XMPPError(
                    "recipient-unavailable", text="Cannot find this mattermost user"
                )

            self.messages_waiting_for_echo.add(msg_id)
            return msg_id

    async def send_file(
        self, chat: Recipient, url: str, http_response, thread=None, **k
    ):
        # assert isinstance(chat, Contact)
        channel_id = await chat.direct_channel_id()  # type:ignore
        file_id = await self.mm_client.upload_file(channel_id, url, http_response)
        return await self.mm_client.send_message_with_file(channel_id, file_id, thread)

    async def active(self, c: Recipient, thread=None):
        pass

    async def inactive(self, c: Recipient, thread=None):
        pass

    async def composing(self, c: Recipient, thread=None):
        # assert isinstance(c, Contact)
        await self.ws.user_typing(await c.direct_channel_id())  # type:ignore

    async def paused(self, c: Recipient, thread=None):
        # no equivalent in MM, seems to have an automatic timeout in clients
        pass

    async def displayed(self, c: Recipient, legacy_msg_id: Any, thread=None):
        # assert isinstance(c, Contact)
        channel = await c.direct_channel_id()  # type:ignore
        f = self.view_futures[channel] = self.xmpp.loop.create_future()
        await self.mm_client.view_channel(channel)
        await f

    async def correct(self, c: Recipient, text: str, legacy_msg_id: Any, thread=None):
        await self.mm_client.update_post(legacy_msg_id, text)

    async def search(self, form_values: dict[str, str]):
        pass

    async def retract(self, c: Recipient, legacy_msg_id: Any, thread=None):
        await self.mm_client.delete_post(legacy_msg_id)

    async def react(
        self, c: Recipient, legacy_msg_id: Any, emojis: list[str], thread=None
    ):
        mm_reactions = await self.get_mm_reactions(
            legacy_msg_id, await self.mm_client.mm_id
        )
        xmpp_reactions = {x for x in emojis}
        self.log.debug("%s vs %s", mm_reactions, xmpp_reactions)
        for e in xmpp_reactions - mm_reactions:
            await self.mm_client.react(legacy_msg_id, e)
        for e in mm_reactions - xmpp_reactions:
            await self.mm_client.delete_reaction(legacy_msg_id, e)

    async def get_mm_reactions(self, legacy_msg_id: str, user_id: Optional[str]):
        return {
            x
            for i, x in await self.mm_client.get_reactions(legacy_msg_id)
            if i == user_id
        }


def get_post_from_dict(data: dict):
    p = Post.from_dict(data)
    assert isinstance(p.user_id, str)
    assert isinstance(p.id, str)
    assert isinstance(p.channel_id, str)
    return p


def get_reaction_from_dict(data: dict):
    r = Reaction.from_dict(data)
    assert isinstance(r.user_id, str)
    assert isinstance(r.emoji_name, str)
    return r
