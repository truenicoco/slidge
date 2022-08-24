import asyncio
import concurrent.futures
import io
import logging
import pprint
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Optional

import aiohttp
import skpy
from slixmpp import JID

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

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        pass


class Roster(LegacyRoster):
    # ':' is forbidden in the username part of a JID

    @staticmethod
    def legacy_id_to_jid_username(legacy_id: str) -> str:
        if legacy_id.startswith("live:"):
            return legacy_id.replace("live:", "__live__")
        else:
            return legacy_id

    @staticmethod
    def jid_username_to_legacy_id(jid_username: str) -> str:
        if jid_username.startswith("__live__"):
            return jid_username.replace("__live__", "live:")
        else:
            return jid_username


class Contact(LegacyContact):
    pass


class Session(BaseSession[Contact, Roster, Gateway]):
    skype_token_path: Path
    sk: skpy.Skype
    thread: Optional[Thread]
    sent_by_user_to_ack: dict[int, asyncio.Future]
    unread_by_user: dict[int, skpy.SkypeMsg]
    send_lock: Lock

    def post_init(self):
        self.skype_token_path = self.xmpp.home_dir / self.user.bare_jid
        self.thread = None
        self.sent_by_user_to_ack = {}
        self.unread_by_user = {}
        self.send_lock = Lock()

    async def async_wrap(self, func, *args):
        return await self.xmpp.loop.run_in_executor(executor, func, *args)

    async def login(self):
        f = self.user.registration_form
        try:
            self.sk = await self.async_wrap(
                skpy.Skype,
                f["username"],
                f["password"],
                str(self.skype_token_path),
            )
        except skpy.core.SkypeApiException:
            # workaround for https://github.com/Terrance/SkPy/issues/164
            # not sure why, but I need this for my (nicoco's) account
            # FWIW, I have a live (I think) a account with a very old skype account (pre-microsoft)
            # and I set up 2FA + app password for slidge
            self.sk = await self.async_wrap(skpy.Skype)
            self.sk.conn.setTokenFile(str(self.skype_token_path))
            self.sk.conn.soapLogin(f["username"], f["password"])

        # self.sk.subscribePresence()
        for contact in self.sk.contacts:
            c = self.contacts.by_legacy_id(contact.id)
            first = contact.name.first
            last = contact.name.last
            if first is not None and last is not None:
                c.name = f"{first} {last}"
            elif first is not None:
                c.name = first
            elif last is not None:
                c.name = last
            if contact.avatar is not None:
                c.avatar = contact.avatar
            await c.add_to_roster()
            c.online()
        # TODO: close this gracefully on exit
        self.thread = thread = Thread(target=self.skype_blocking)
        thread.start()
        return f"Connected as '{self.sk.userId}'"

    def skype_blocking(self):
        while True:
            for event in self.sk.getEvents():
                # no need to sleep since getEvents blocks for 30 seconds already
                asyncio.run_coroutine_threadsafe(
                    self.on_skype_event(event), self.xmpp.loop
                )

    async def on_skype_event(self, event: skpy.SkypeEvent):
        log.debug("Skype event: %s", event)
        if isinstance(event, skpy.SkypeNewMessageEvent):
            while self.send_lock.locked():
                await asyncio.sleep(0.1)
            msg = event.msg
            chat = event.msg.chat
            if isinstance(chat, skpy.SkypeSingleChat):
                log.debug("this is a single chat with user: %s", chat.userIds[0])
                contact = self.contacts.by_legacy_id(chat.userIds[0])
                if msg.userId == self.sk.userId:
                    try:
                        fut = self.sent_by_user_to_ack.pop(msg.id)
                    except KeyError:
                        if log.isEnabledFor(logging.DEBUG):
                            log.debug(
                                "Slidge did not send this message: %s",
                                pprint.pformat(vars(event)),
                            )
                        contact.carbon(msg.plain)
                    else:
                        fut.set_result(msg)
                else:
                    if isinstance(msg, skpy.SkypeTextMsg):
                        contact.send_text(msg.plain, legacy_msg_id=msg.id)
                        self.unread_by_user[msg.id] = msg
                    elif isinstance(msg, skpy.SkypeFileMsg):
                        file = io.BytesIO(
                            await self.async_wrap(lambda: msg.fileContent)
                        )  # non-blocking download / lambda because fileContent = property
                        await contact.send_file(filename=msg.file.name, input_file=file)
        elif isinstance(event, skpy.SkypeTypingEvent):
            contact = self.contacts.by_legacy_id(event.userId)
            if event.active:
                contact.composing()
            else:
                contact.paused()
        elif isinstance(event, skpy.SkypeChatUpdateEvent):
            if log.isEnabledFor(logging.DEBUG):
                log.debug("chat update: %s", pprint.pformat(vars(event)))
        # No 'contact has read' event :( https://github.com/Terrance/SkPy/issues/206
        await self.async_wrap(event.ack)

    async def send_text(self, t: str, c: LegacyContact, *, reply_to_msg_id=None):
        chat = self.sk.contacts[c.legacy_id].chat
        self.send_lock.acquire()
        msg = await self.async_wrap(chat.sendMsg, t)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Sent msg: %s", pprint.pformat(vars(msg)))
        future = asyncio.Future[skpy.SkypeMsg]()
        self.sent_by_user_to_ack[msg.id] = future
        self.send_lock.release()
        skype_msg = await future
        return skype_msg.id

    async def logout(self):
        pass

    async def send_file(self, u: str, c: LegacyContact, *, reply_to_msg_id=None):
        async with aiohttp.ClientSession() as session:
            async with session.get(u) as response:
                file_bytes = await response.read()
        fname = u.split("/")[-1]
        fname_lower = fname.lower()
        await self.async_wrap(
            self.sk.contacts[c.legacy_id].chat.sendFile,
            io.BytesIO(file_bytes),
            fname,
            any(fname_lower.endswith(x) for x in (".png", ".jpg", ".gif", ".jpeg")),
        )

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        executor.submit(self.sk.contacts[c.legacy_id].chat.setTyping, True)

    async def paused(self, c: LegacyContact):
        executor.submit(self.sk.contacts[c.legacy_id].chat.setTyping, False)

    async def displayed(self, legacy_msg_id: int, c: LegacyContact):
        try:
            skype_msg = self.unread_by_user.pop(legacy_msg_id)
        except KeyError:
            log.debug(
                "We did not transmit: %s (%s)", legacy_msg_id, self.unread_by_user
            )
        else:
            # FIXME: this raises HTTP 400 and does not mark the message as read
            # https://github.com/Terrance/SkPy/issues/207
            log.debug("Calling read on %s", skype_msg)
            await self.async_wrap(skype_msg.read)

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def search(self, form_values: dict[str, str]):
        pass


executor = (
    concurrent.futures.ThreadPoolExecutor()
)  # TODO: close this gracefully on exit

log = logging.getLogger(__name__)
