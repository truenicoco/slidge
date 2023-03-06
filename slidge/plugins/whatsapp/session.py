import asyncio
from asyncio import iscoroutine, run_coroutine_threadsafe
from datetime import datetime, timezone
from functools import wraps
from os import remove
from os.path import basename
from shelve import open
from typing import Optional, Union

from slidge import BaseSession, GatewayUser, global_config
from slidge.plugins.whatsapp.generated import go, whatsapp  # type:ignore

from .contact import Contact, Roster
from .gateway import Gateway
from .group import MUC, Bookmarks

MESSAGE_PAIR_SUCCESS = (
    "Pairing successful! You might need to repeat this process in the future if the Linked Device is "
    "re-registered from your main device."
)

MESSAGE_LOGGED_OUT = (
    "You have been logged out, please use the re-login adhoc command "
    "and re-scan the QR code on your main device."
)


Recipient = Union[Contact, MUC]


class Session(BaseSession[str, Recipient]):
    xmpp: Gateway
    contacts: Roster
    bookmarks: Bookmarks

    def __init__(self, user: GatewayUser):
        super().__init__(user)
        self.user_shelf_path = (
            global_config.HOME_DIR / "whatsapp" / (self.user.bare_jid + ".shelf")
        )
        with open(str(self.user_shelf_path)) as shelf:
            try:
                device = whatsapp.LinkedDevice(ID=shelf["device_id"])
            except KeyError:
                device = whatsapp.LinkedDevice()
        self.whatsapp = self.xmpp.whatsapp.NewSession(device)
        self._handle_event = make_sync(self.handle_event, self.xmpp.loop)
        self.whatsapp.SetEventHandler(self._handle_event)

    def shutdown(self):
        for c in self.contacts:
            c.offline()
        self.xmpp.loop.create_task(self.disconnect())

    async def login(self):
        """
        Initiate login process and connect session to WhatsApp. Depending on existing state, login
        might either return having initiated the Linked Device registration process in the background,
        or will re-connect to a previously existing Linked Device session.
        """
        self.whatsapp.Login()
        self._connected: asyncio.Future[str] = self.xmpp.loop.create_future()
        return await self._connected

    async def logout(self):
        """
        Logout from the active WhatsApp session. This will also force a remote log-out, and thus
        require pairing on next login. For simply disconnecting the active session, look at the
        :meth:`.Session.disconnect` function.
        """
        self.whatsapp.Logout()
        remove(self.user_shelf_path)

    async def disconnect(self):
        """
        Disconnect the active WhatsApp session. This will not remove any local or remote state, and
        will thus allow previously authenticated sessions to re-authenticate without needing to pair.
        """
        self.whatsapp.Disconnect()

    async def handle_event(self, event, ptr):
        """
        Handle incoming event, as propagated by the WhatsApp adapter. Typically, events carry all
        state required for processing by the Gateway itself, and will do minimal processing themselves.
        """
        data = whatsapp.EventPayload(handle=ptr)
        if event == whatsapp.EventQRCode:
            self.send_gateway_status("QR Scan Needed", show="dnd")
            await self.send_qr(data.QRCode)
        elif event == whatsapp.EventPair:
            self.send_gateway_message(MESSAGE_PAIR_SUCCESS)
            with open(str(self.user_shelf_path)) as shelf:
                shelf["device_id"] = data.PairDeviceID
        elif event == whatsapp.EventConnected:
            if not self._connected.done():
                self.contacts.user_legacy_id = data.ConnectedJID
                self._connected.set_result("Connected")
        elif event == whatsapp.EventLoggedOut:
            self.logged = False
            self.send_gateway_message(MESSAGE_LOGGED_OUT)
            self.send_gateway_status("Logged out", show="away")
        elif event == whatsapp.EventContact:
            await self.contacts.add_whatsapp_contact(data.Contact)
        elif event == whatsapp.EventGroup:
            await self.bookmarks.add_whatsapp_group(data.Group)
        elif event == whatsapp.EventPresence:
            contact = await self.contacts.by_legacy_id(data.Presence.JID)
            await contact.update_presence(data.Presence.Away, data.Presence.LastSeen)
        elif event == whatsapp.EventChatState:
            await self.handle_chat_state(data.ChatState)
        elif event == whatsapp.EventReceipt:
            await self.handle_receipt(data.Receipt)
        elif event == whatsapp.EventCall:
            await self.handle_call(data.Call)
        elif event == whatsapp.EventMessage:
            await self.handle_message(data.Message)

    async def handle_chat_state(self, state: whatsapp.ChatState):
        contact = await self.get_contact_or_participant(state.JID, state.GroupJID)
        if state.Kind == whatsapp.ChatStateComposing:
            contact.composing()
        elif state.Kind == whatsapp.ChatStatePaused:
            contact.paused()

    async def handle_receipt(self, receipt: whatsapp.Receipt):
        """
        Handle incoming delivered/read receipt, as propagated by the WhatsApp adapter.
        """
        contact = await self.get_contact_or_participant(receipt.JID, receipt.GroupJID)
        for message_id in receipt.MessageIDs:
            if receipt.Kind == whatsapp.ReceiptDelivered:
                contact.received(message_id)
            elif receipt.Kind == whatsapp.ReceiptRead:
                contact.displayed(legacy_msg_id=message_id, carbon=receipt.IsCarbon)

    async def handle_call(self, call: whatsapp.Call):
        contact = await self.contacts.by_legacy_id(call.JID)
        if call.State == whatsapp.CallMissed:
            text = "Missed call"
        text = text + f" from {contact.name} (xmpp:{contact.jid.bare})"
        if call.Timestamp > 0:
            call_at = datetime.fromtimestamp(call.Timestamp, tz=timezone.utc)
            text = text + f" at {call_at}"
        self.send_gateway_message(text)

    async def handle_message(self, message: whatsapp.Message):
        """
        Handle incoming message, as propagated by the WhatsApp adapter. Messages can be one of many
        types, including plain-text messages, media messages, reactions, etc., and may also include
        other aspects such as references to other messages for the purposes of quoting or correction.
        """
        contact = await self.get_contact_or_participant(message.JID, message.GroupJID)
        message_reply_id = message.ReplyID if message.ReplyID else None
        message_reply_body = message.ReplyBody if message.ReplyBody else None
        message_reply_to = None  # MUCs only
        message_reply_self = message.OriginJID == message.JID  # 1:1 only
        if message.GroupJID and message.OriginJID:
            muc = await self.bookmarks.by_legacy_id(message.GroupJID)
            message_reply_to = await muc.get_participant_by_legacy_id(message.OriginJID)
        message_timestamp = (
            datetime.fromtimestamp(message.Timestamp, tz=timezone.utc)
            if message.Timestamp > 0
            else None
        )
        if message.Kind == whatsapp.MessagePlain:
            contact.send_text(
                body=message.Body,
                legacy_msg_id=message.ID,
                when=message_timestamp,
                reply_to_msg_id=message_reply_id,
                reply_to_fallback_text=message_reply_body,
                reply_to_author=message_reply_to,  # only used in mucs
                reply_self=message_reply_self,  # only used in 1:1
                carbon=message.IsCarbon,
            )
        elif message.Kind == whatsapp.MessageAttachment:
            for ptr in message.Attachments:
                attachment = whatsapp.Attachment(handle=ptr)
                attachment_caption = attachment.Caption if attachment.Caption else None
                await contact.send_file(
                    file_path=None,
                    file_name=attachment.Filename,
                    content_type=attachment.MIME,
                    data=bytes(attachment.Data),
                    legacy_msg_id=message.ID,
                    reply_to_msg_id=message_reply_id,
                    when=message_timestamp,
                    caption=attachment_caption,
                    carbon=message.IsCarbon,
                )
        elif message.Kind == whatsapp.MessageRevoke:
            contact.retract(legacy_msg_id=message.ID, carbon=message.IsCarbon)
        elif message.Kind == whatsapp.MessageReaction:
            emojis = [message.Body] if message.Body else []
            contact.react(
                legacy_msg_id=message.ID, emojis=emojis, carbon=message.IsCarbon
            )

    async def send_text(
        self,
        chat: Recipient,
        text: str,
        *,
        reply_to_msg_id: Optional[str] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to=None,
        **_,
    ):
        """
        Send outgoing plain-text message to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message = whatsapp.Message(ID=message_id, JID=chat.legacy_id, Body=text)
        if reply_to_msg_id:
            message.ReplyID = reply_to_msg_id
        if reply_to:
            message.OriginJID = (
                reply_to.contact.legacy_id if chat.is_group else chat.legacy_id
            )
        if reply_to_fallback_text:
            message.ReplyBody = strip_quote_prefix(reply_to_fallback_text)
            message.Body = message.Body.lstrip()
        self.whatsapp.SendMessage(message)
        return message_id

    async def send_file(
        self,
        chat: Recipient,
        url: str,
        http_response,
        reply_to_msg_id: Optional[str] = None,
        **_,
    ):
        """
        Send outgoing media message (i.e. audio, image, document) to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message_attachment = whatsapp.Attachment(
            MIME=http_response.content_type, Filename=basename(url), URL=url
        )
        message = whatsapp.Message(
            Kind=whatsapp.MessageAttachment,
            ID=message_id,
            JID=chat.legacy_id,
            ReplyID=reply_to_msg_id if reply_to_msg_id else "",
            Attachments=whatsapp.Slice_whatsapp_Attachment([message_attachment]),
        )
        self.whatsapp.SendMessage(message)
        return message_id

    async def active(self, c: Recipient, thread=None):
        """
        WhatsApp has no equivalent to the "active" chat state, so calls to this function are no-ops.
        """
        pass

    async def inactive(self, c: Recipient, thread=None):
        """
        WhatsApp has no equivalent to the "inactive" chat state, so calls to this function are no-ops.
        """
        pass

    async def composing(self, c: Recipient, thread=None):
        """
        Send "composing" chat state to given WhatsApp contact, signifying that a message is currently
        being composed.
        """
        state = whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStateComposing)
        self.whatsapp.SendChatState(state)

    async def paused(self, c: Recipient, thread=None):
        """
        Send "paused" chat state to given WhatsApp contact, signifying that an (unsent) message is no
        longer being composed.
        """
        state = whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStatePaused)
        self.whatsapp.SendChatState(state)

    async def displayed(self, c: Recipient, legacy_msg_id: str, thread=None):
        """
        Send "read" receipt, signifying that the WhatsApp message sent has been displayed on the XMPP
        client.
        """
        receipt = whatsapp.Receipt(
            MessageIDs=go.Slice_string([legacy_msg_id]),
            JID=c.get_message_sender(legacy_msg_id) if c.is_group else c.legacy_id,  # type: ignore
            GroupJID=c.legacy_id if c.is_group else "",
        )
        self.whatsapp.SendReceipt(receipt)

    async def react(
        self, c: Recipient, legacy_msg_id: str, emojis: list[str], thread=None
    ):
        """
        Send or remove emoji reaction to existing WhatsApp message.
        Slidge core makes sure that the emojis parameter is always empty or a
        *single* emoji.
        """
        is_carbon = self._is_carbon(c, legacy_msg_id)
        message_sender_id = (
            c.get_message_sender(legacy_msg_id) if not is_carbon and c.is_group else ""  # type: ignore
        )
        message = whatsapp.Message(
            Kind=whatsapp.MessageReaction,
            ID=legacy_msg_id,
            JID=c.legacy_id,
            OriginJID=message_sender_id,
            Body=emojis[0] if emojis else "",
            IsCarbon=is_carbon,
        )
        self.whatsapp.SendMessage(message)

    async def retract(self, c: Recipient, legacy_msg_id: str, thread=None):
        """
        Request deletion (aka retraction) for a given WhatsApp message.
        """
        message = whatsapp.Message(
            Kind=whatsapp.MessageRevoke, ID=legacy_msg_id, JID=c.legacy_id
        )
        self.whatsapp.SendMessage(message)

    async def correct(self, c: Recipient, text: str, legacy_msg_id: str, thread=None):
        pass

    async def search(self, form_values: dict[str, str]):
        self.send_gateway_message("Searching on WhatsApp has not been implemented yet.")

    async def get_contact_or_participant(
        self, legacy_contact_id: str, legacy_group_jid: str
    ):
        """
        Return either a Contact or a Participant instance for the given contact and group JIDs.
        """
        if legacy_group_jid:
            muc = await self.bookmarks.by_legacy_id(legacy_group_jid)
            return await muc.get_participant_by_legacy_id(legacy_contact_id)
        else:
            return await self.contacts.by_legacy_id(legacy_contact_id)

    def _is_carbon(self, c: Recipient, legacy_msg_id: str):
        if c.is_group:
            return legacy_msg_id in self.muc_sent_msg_ids
        else:
            return legacy_msg_id in self.sent


def make_sync(func, loop):
    """
    Wrap async function in synchronous operation, running against the given loop in thread-safe mode.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if iscoroutine(result):
            future = run_coroutine_threadsafe(result, loop)
            return future.result()
        return result

    return wrapper


def strip_quote_prefix(text: str):
    """
    Return multi-line text without leading quote marks (i.e. the ">" character).
    """
    return "\n".join(x.lstrip(">").strip() for x in text.split("\n")).strip()
