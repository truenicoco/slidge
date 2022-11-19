import asyncio
import io
import logging
import pprint
import threading
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Optional

import aiohttp
import skpy
from requests.exceptions import ConnectionError
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *


class Gateway(BaseGateway["Session"]):
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

    def shutdown(self):
        super().shutdown()
        log.debug("Shutting down user threads")
        for user in user_store.get_all():
            session = self._session_cls.from_jid(user.jid)
            if thread := session.thread:
                thread.stop()


class Contact(LegacyContact):
    def update_presence(self, status: skpy.SkypeUtils.Status):
        if status == skpy.SkypeUtils.Status.Offline:
            self.offline()
        elif status == skpy.SkypeUtils.Status.Busy:
            self.busy()
        elif status == skpy.SkypeUtils.Status.Away:
            self.away("Away")
        elif status == skpy.SkypeUtils.Status.Idle:
            self.away("Idle")
        elif status == skpy.SkypeUtils.Status.Online:
            self.online()
        else:
            log.warning("Unknown contact status: %s", status)


class ListenThread(Thread):
    def __init__(self, session: "Session", *a, **kw):
        super().__init__(*a, **kw, daemon=True)
        self.name = f"listen-{session.user.bare_jid}"
        self.session = session
        self._target = self.skype_blocking
        self.stop_event = threading.Event()

    def skype_blocking(self):
        session = self.session
        sk = session.sk
        loop = session.xmpp.loop
        while True:
            if self.stop_event.is_set():
                break
            for event in sk.getEvents():
                # no need to sleep since getEvents blocks for 30 seconds already
                asyncio.run_coroutine_threadsafe(session.on_skype_event(event), loop)

    def stop(self):
        self.stop_event.set()


class Session(BaseSession[Contact, LegacyRoster, Gateway]):
    skype_token_path: Path
    sk: skpy.Skype

    def __init__(self, user):
        super().__init__(user)
        self.skype_token_path = global_config.HOME_DIR / self.user.bare_jid
        self.thread: Optional[ListenThread] = None
        self.sent_by_user_to_ack = dict[int, asyncio.Future]()
        self.unread_by_user = dict[int, skpy.SkypeMsg]()
        self.send_lock = Lock()

    async def login(self):
        f = self.user.registration_form
        self.sk = await asyncio.to_thread(
            skpy.Skype,
            f["username"],
            f["password"],
            str(self.skype_token_path),
        )

        self.sk.subscribePresence()
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
        # TODO: Creating 1 thread per user is probably very not optimal.
        #       We should contribute to skpy to make it aiohttp compatible…
        self.thread = thread = ListenThread(self)
        thread.start()
        return f"Connected as '{self.sk.userId}'"

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
                        fut = self.sent_by_user_to_ack.pop(msg.clientId)
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
                        contact.send_text(msg.plain, legacy_msg_id=msg.clientId)
                        self.unread_by_user[msg.clientId] = msg
                    elif isinstance(msg, skpy.SkypeFileMsg):
                        file = io.BytesIO(
                            await asyncio.to_thread(lambda: msg.fileContent)
                        )  # non-blocking download / lambda because fileContent = property
                        await contact.send_file(filename=msg.file.name, input_file=file)
        elif isinstance(event, skpy.SkypeTypingEvent):
            contact = self.contacts.by_legacy_id(event.userId)
            if event.active:
                contact.composing()
            else:
                contact.paused()
        elif isinstance(event, skpy.SkypeEditMessageEvent):
            msg = event.msg
            chat = event.msg.chat
            if isinstance(chat, skpy.SkypeSingleChat):
                if (user_id := msg.userId) != self.sk.userId:
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("edit msg event: %s", pprint.pformat(vars(event)))
                    contact = self.contacts.by_legacy_id(user_id)
                    msg_id = msg.clientId
                    log.debug("edited msg id: %s", msg_id)
                    if text := msg.plain:
                        contact.correct(msg_id, text)
                    else:
                        if msg_id:
                            contact.retract(msg_id)
                        else:
                            contact.send_text(
                                "/me tried to remove a message, but slidge got in trouble"
                            )
        elif isinstance(event, skpy.SkypeChatUpdateEvent):
            if log.isEnabledFor(logging.DEBUG):
                log.debug("chat update: %s", pprint.pformat(vars(event)))
        elif isinstance(event, skpy.SkypePresenceEvent):
            if event.userId != self.sk.userId:
                self.contacts.by_legacy_id(event.userId).update_presence(event.status)

        # No 'contact has read' event :( https://github.com/Terrance/SkPy/issues/206
        await asyncio.to_thread(event.ack)

    async def send_text(self, t: str, c: LegacyContact, *, reply_to_msg_id=None):
        chat = self.sk.contacts[c.legacy_id].chat
        self.send_lock.acquire()
        msg = await asyncio.to_thread(chat.sendMsg, t)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Sent msg: %s", pprint.pformat(vars(msg)))
        future = asyncio.Future[skpy.SkypeMsg]()
        self.sent_by_user_to_ack[msg.clientId] = future
        self.send_lock.release()
        skype_msg = await future
        return skype_msg.clientId

    async def logout(self):
        if self.thread is not None:
            self.thread.stop()
            self.thread.join()

    async def send_file(self, u: str, c: LegacyContact, *, reply_to_msg_id=None):
        async with aiohttp.ClientSession() as session:
            async with session.get(u) as response:
                file_bytes = await response.read()
        fname = u.split("/")[-1]
        fname_lower = fname.lower()
        await asyncio.to_thread(
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
        await asyncio.to_thread(self.sk.contacts[c.legacy_id].chat.setTyping, True)

    async def paused(self, c: LegacyContact):
        await asyncio.to_thread(self.sk.contacts[c.legacy_id].chat.setTyping, False)

    async def displayed(self, legacy_msg_id: int, c: LegacyContact):
        try:
            skype_msg = self.unread_by_user.pop(legacy_msg_id)
        except KeyError:
            log.debug(
                "We did not transmit: %s (%s)", legacy_msg_id, self.unread_by_user
            )
        else:
            log.debug("Calling read on %s", skype_msg)
            try:
                await asyncio.to_thread(skype_msg.read)
            except skpy.SkypeApiException as e:
                # FIXME: this raises HTTP 400 and does not mark the message as read
                # https://github.com/Terrance/SkPy/issues/207
                self.log.debug("Skype read marker failed: %r", e)

    async def correct(self, text: str, legacy_msg_id: Any, c: Contact):
        m = self.get_msg(legacy_msg_id, c)
        await asyncio.to_thread(m.edit, text)

    async def retract(self, legacy_msg_id: Any, c: Contact):
        m = self.get_msg(legacy_msg_id, c)
        log.debug("Deleting %s", m)
        await asyncio.to_thread(m.delete)

    async def search(self, form_values: dict[str, str]):
        pass

    def get_msg(self, legacy_msg_id: int, contact: Contact) -> skpy.SkypeTextMsg:
        for m in self.sk.contacts[contact.legacy_id].chat.getMsgs():
            log.debug("Message %r vs %r : %s", legacy_msg_id, m.clientId, m)
            if m.clientId == legacy_msg_id:
                return m
        else:
            raise XMPPError(
                "item-not-found", text=f"Could not find message '{legacy_msg_id}'"
            )


def handle_thread_exception(args):
    if (
        (thread := getattr(args, "thread"))
        and isinstance(thread, ListenThread)
        and args.exc_type is ConnectionError
    ):
        session = thread.session
        log.info("Connection error, attempting re-login for %s", session.user)
        thread.stop()
        session.re_login()
    else:
        log.error("Exception in thread: %s", args)
        raise RuntimeError(args)


threading.excepthook = handle_thread_exception

log = logging.getLogger(__name__)
