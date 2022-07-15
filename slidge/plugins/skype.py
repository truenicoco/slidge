import asyncio
import logging
from pathlib import Path
from threading import Thread
from typing import Optional

import aiohttp
import skpy

from slixmpp import JID, Presence

from slidge import *


class Gateway(BaseGateway):
    REGISTRATION_INSTRUCTIONS = "Enter skype credentials"
    REGISTRATION_FIELDS = [
        FormField(var="username", label="Username", required=True),
        FormField(var="password", label="Password", required=True, private=True),
    ]

    ROSTER_GROUP = "Skype"

    COMPONENT_NAME = "Skype (slidge)"
    COMPONENT_TYPE = "skype"

    COMPONENT_AVATAR = "https://logodownload.org/wp-content/uploads/2017/05/skype-logo-1-1-2048x2048.png"

    async def validate(self, user_jid: JID, registration_form: dict[str, str]):
        pass


class Session(BaseSession[LegacyContact, LegacyRoster]):
    skype_token_path: Path
    sk: skpy.Skype
    thread: Optional[Thread]

    def post_init(self):
        self.skype_token_path = self.xmpp.home_dir / self.user.bare_jid
        self.thread = None

    async def login(self, p: Presence):
        f = self.user.registration_form
        self.sk = skpy.Skype(f["username"], f["password"], str(self.skype_token_path))
        for contact in self.sk.contacts:
            if ":" in contact.id:
                log.debug("Ignoring contact: %s", contact)
                continue
            c = self.contacts.by_legacy_id(contact.id)
            if contact.avatar is not None:
                async with aiohttp.ClientSession() as session:
                    async with session.get(contact.avatar) as response:
                        avatar_bytes = await response.read()
                c.avatar = avatar_bytes
            await c.add_to_roster()
            c.online()
        self.thread = thread = Thread(target=self.skype_blocking)
        thread.start()

    def skype_blocking(self):
        while True:
            for event in self.sk.getEvents():
                # no need to sleep since getEvents blocks for 30 seconds already
                log.debug("New skype event")
                asyncio.run_coroutine_threadsafe(
                    self.on_skype_event(event), self.xmpp.loop
                )

    async def on_skype_event(self, event: skpy.SkypeEvent):
        log.debug("Skype event: %s", event)
        event.ack()
        if isinstance(event, skpy.SkypeNewMessageEvent):
            msg = event.msg
            chat = event.msg.chat
            log.debug("new msg: %s in chat: %s", msg, chat)
            if isinstance(chat, skpy.SkypeSingleChat):
                log.debug("this is a single chat with user: %s", chat.userIds[0])
                contact = self.contacts.by_legacy_id(chat.userIds[0])
                if msg.userId == self.sk.userId:
                    contact.carbon(msg.plain)
                else:
                    contact.send_text(msg.plain)

        elif isinstance(event, skpy.SkypeChatUpdateEvent):
            log.debug("chat update: %s", event.ChatId)

    async def send_text(self, t: str, c: LegacyContact):
        chat = self.sk.contacts[c.legacy_id].chat
        log.debug("Skype chat: %s", chat)
        msg = chat.sendMsg(t)
        log.debug("Sent msg %s", msg)


log = logging.getLogger(__name__)
