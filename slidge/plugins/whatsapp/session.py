import asyncio
from asyncio import iscoroutine, run_coroutine_threadsafe
from datetime import datetime
from functools import wraps
from io import BytesIO
from mimetypes import guess_type
from os import remove
from os.path import basename
from shelve import open
from typing import Optional, Union

from slixmpp.exceptions import XMPPError

from slidge import (
    BaseSession,
    GatewayUser,
    LegacyBookmarks,
    LegacyMUC,
    LegacyParticipant,
    global_config,
)
from slidge.plugins.whatsapp.generated import go, whatsapp

from . import config
from .contact import Contact, Roster
from .gateway import Gateway

MESSAGE_PAIR_SUCCESS = (
    "Pairing successful! You might need to repeat this process in the future if the Linked Device is "
    "re-registered from your main device."
)

MESSAGE_LOGGED_OUT = "You have been logged out, please re-scan the QR code on your main device to log in."


class Session(
    BaseSession[
        Gateway, str, Roster, Contact, LegacyBookmarks, LegacyMUC, LegacyParticipant
    ]
):
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
        self._connected: asyncio.Future[str] = self.xmpp.loop.create_future()

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
        try:
            self.whatsapp.Login()
        except RuntimeError as err:
            raise XMPPError(text=str(err))
        return await self._connected

    async def logout(self):
        """
        Logout from the active WhatsApp session. This will also force a remote log-out, and thus
        require pairing on next login. For simply disconnecting the active session, look at the
        :meth:`.Session.disconnect` function.
        """
        try:
            self.whatsapp.Logout()
        except RuntimeError as err:
            raise XMPPError(text=str(err))
        remove(self.user_shelf_path)

    async def disconnect(self):
        """
        Disconnect the active WhatsApp session. This will not remove any local or remote state, and
        will thus allow previously authenticated sessions to re-authenticate without needing to pair.
        """
        try:
            self.whatsapp.Disconnect()
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def handle_event(self, event, ptr):
        """
        Handle incoming event, as propagated by the WhatsApp adapter. Typically, events carry all
        state required for processing by the Gateway itself, and will do minimal processing themselves.
        """
        data = whatsapp.EventPayload(handle=ptr)
        if event == whatsapp.EventQRCode:
            self.send_gateway_status("QR Scan Needed", show="dnd")
            await self.send_qr(data.QRCode)
        elif event == whatsapp.EventPairSuccess:
            self.send_gateway_message(MESSAGE_PAIR_SUCCESS)
            with open(str(self.user_shelf_path)) as shelf:
                shelf["device_id"] = data.PairDeviceID
            try:
                self.whatsapp.FetchRoster(refresh=True)
            except RuntimeError as err:
                self.log.error("Failed refreshing roster on pair: %s", str(err))
        elif event == whatsapp.EventConnected:
            try:
                self.whatsapp.FetchRoster(refresh=config.ALWAYS_SYNC_ROSTER)
            except RuntimeError as err:
                self.log.error("Failed refreshing roster on connect: %s", str(err))
            try:
                self._connected.set_result("Connected")
            except asyncio.InvalidStateError:
                # FIXME: login should be made blocking, and/or a "socket disconnected" event
                #        should be thrown, so we can properly reset the future
                self.log.debug(
                    "We thought we were connected but apparently we weren't?"
                )
        elif event == whatsapp.EventLoggedOut:
            self._connected = self.xmpp.loop.create_future()
            self.send_gateway_message(MESSAGE_LOGGED_OUT)
            self.send_gateway_status("Logged out", show="away")
            await self.login()
        elif event == whatsapp.EventContactSync:
            contact = await self.contacts.by_legacy_id(data.Contact.JID)
            contact.name = data.Contact.Name
            if data.Contact.AvatarURL != "":
                contact.avatar = data.Contact.AvatarURL
            await contact.add_to_roster()
        elif event == whatsapp.EventPresence:
            contact = await self.contacts.by_legacy_id(data.Presence.JID)
            contact.update_presence(data.Presence.Away, data.Presence.LastSeen)
        elif event == whatsapp.EventChatState:
            contact = await self.contacts.by_legacy_id(data.ChatState.JID)
            if data.ChatState.Kind == whatsapp.ChatStateComposing:
                contact.composing()
            elif data.ChatState.Kind == whatsapp.ChatStatePaused:
                contact.paused()
        elif event == whatsapp.EventReceipt:
            await self.handle_receipt(data.Receipt)
        elif event == whatsapp.EventCall:
            contact = await self.contacts.by_legacy_id(data.Call.JID)
            if data.Call.State == whatsapp.CallMissed:
                text = "Missed call"
            text = text + f" from {contact.name} (xmpp:{contact.jid.bare})"
            if data.Call.Timestamp > 0:
                text = text + f" at {datetime.fromtimestamp(data.Call.Timestamp)}"
            self.send_gateway_message(text)
        elif event == whatsapp.EventMessage:
            await self.handle_message(data.Message)

    async def handle_receipt(self, receipt: whatsapp.Receipt):
        """
        Handle incoming delivered/read receipt, as propagated by the WhatsApp adapter.
        """
        contact = await self.contacts.by_legacy_id(receipt.JID)
        for message_id in receipt.MessageIDs:
            if receipt.Kind == whatsapp.ReceiptDelivered:
                contact.received(message_id)
            elif receipt.Kind == whatsapp.ReceiptRead:
                contact.displayed(legacy_msg_id=message_id, carbon=receipt.IsCarbon)

    async def handle_message(self, message: whatsapp.Message):
        """
        Handle incoming message, as propagated by the WhatsApp adapter. Messages can be one of many
        types, including plain-text messages, media messages, reactions, etc., and may also include
        other aspects such as references to other messages for the purposes of quoting or correction.
        """
        contact = await self.contacts.by_legacy_id(message.JID)
        message_reply_id = message.ReplyID if message.ReplyID != "" else None
        message_reply_body = message.ReplyBody if message.ReplyBody != "" else None
        message_timestamp = (
            datetime.fromtimestamp(message.Timestamp) if message.Timestamp > 0 else None
        )
        if message.Kind == whatsapp.MessagePlain:
            contact.send_text(
                body=message.Body,
                legacy_msg_id=message.ID,
                when=message_timestamp,
                reply_to_msg_id=message_reply_id,
                reply_to_fallback_text=message_reply_body,
                carbon=message.IsCarbon,
            )
        elif message.Kind == whatsapp.MessageAttachment:
            for ptr in message.Attachments:
                attachment = whatsapp.Attachment(handle=ptr)
                attachment_caption = (
                    attachment.Caption if attachment.Caption != "" else None
                )
                await contact.send_file(
                    filename=attachment.Filename,
                    content_type=attachment.MIME,
                    input_file=BytesIO(initial_bytes=bytes(attachment.Data)),
                    legacy_msg_id=message.ID,
                    reply_to_msg_id=message_reply_id,
                    when=message_timestamp,
                    caption=attachment_caption,
                    carbon=message.IsCarbon,
                )
        elif message.Kind == whatsapp.MessageRevoke:
            contact.retract(legacy_msg_id=message.ID, carbon=message.IsCarbon)
        elif message.Kind == whatsapp.MessageReaction:
            contact.react(
                legacy_msg_id=message.ID, emojis=message.Body, carbon=message.IsCarbon
            )

    async def send_text(
        self,
        text: str,
        chat: Union[Contact, LegacyMUC],
        reply_to_msg_id: Optional[str] = None,
        reply_to_fallback_text: Optional[str] = None,
        **_,
    ):
        """
        Send outgoing plain-text message to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message = whatsapp.Message(ID=message_id, JID=chat.legacy_id, Body=text)
        if reply_to_msg_id is not None:
            message.ReplyID = reply_to_msg_id
        if reply_to_fallback_text is not None:
            message.ReplyBody = strip_quote_prefix(reply_to_fallback_text)
            message.Body = message.Body.lstrip()
        try:
            self.whatsapp.SendMessage(message)
        except RuntimeError as err:
            raise XMPPError(text=str(err))
        return message_id

    async def send_file(
        self,
        url: str,
        chat: Union[Contact, LegacyMUC],
        reply_to_msg_id: Optional[str] = None,
        **_,
    ):
        """
        Send outgoing media message (i.e. audio, image, document) to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message_attachment = whatsapp.Attachment(
            MIME=guess_type(url)[0], Filename=basename(url), URL=url
        )
        message = whatsapp.Message(
            Kind=whatsapp.MessageAttachment,
            ID=message_id,
            JID=chat.legacy_id,
            ReplyID=reply_to_msg_id if reply_to_msg_id is not None else "",
            Attachments=whatsapp.Slice_whatsapp_Attachment([message_attachment]),
        )
        try:
            self.whatsapp.SendMessage(message)
        except RuntimeError as err:
            raise XMPPError(text=str(err))
        return message_id

    async def active(self, c: Contact):
        """
        WhatsApp has no equivalent to the "active" chat state, so calls to this function are no-ops.
        """
        pass

    async def inactive(self, c: Contact):
        """
        WhatsApp has no equivalent to the "inactive" chat state, so calls to this function are no-ops.
        """
        pass

    async def composing(self, c: Contact):
        """
        Send "composing" chat state to given WhatsApp contact, signifying that a message is currently
        being composed.
        """
        state = whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStateComposing)
        try:
            self.whatsapp.SendChatState(state)
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def paused(self, c: Contact):
        """
        Send "paused" chat state to given WhatsApp contact, signifying that an (unsent) message is no
        longer being composed.
        """
        state = whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStatePaused)
        try:
            self.whatsapp.SendChatState(state)
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def displayed(self, legacy_msg_id: str, c: Contact):
        """
        Send "read" receipt, signifying that the WhatsApp message sent has been displayed on the XMPP
        client.
        """
        receipt = whatsapp.Receipt(
            MessageIDs=go.Slice_string([legacy_msg_id]), JID=c.legacy_id
        )
        try:
            self.whatsapp.SendReceipt(receipt)
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def react(self, legacy_msg_id: str, emojis: list[str], c: Contact):
        """
        Send or remove emoji reaction to existing WhatsApp message.
        Slidge core makes sure that the emojis parameter is always empty or a
        *single* emoji.
        """
        message = whatsapp.Message(
            Kind=whatsapp.MessageReaction,
            ID=legacy_msg_id,
            JID=c.legacy_id,
            Body=emojis[0] if emojis else "",
            IsCarbon=legacy_msg_id in self.sent,
        )
        try:
            self.whatsapp.SendMessage(message)
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def retract(self, legacy_msg_id: str, c: Contact):
        """
        Request deletion (aka retraction) for a given WhatsApp message.
        """
        message = whatsapp.Message(
            Kind=whatsapp.MessageRevoke, ID=legacy_msg_id, JID=c.legacy_id
        )
        try:
            self.whatsapp.SendMessage(message)
        except RuntimeError as err:
            raise XMPPError(text=str(err))

    async def correct(self, text: str, legacy_msg_id: str, c: Contact):
        self.send_gateway_message(
            "Warning: WhatsApp does not support message editing at this point in time."
        )

    async def search(self, form_values: dict[str, str]):
        self.send_gateway_message("Searching on WhatsApp has not been implemented yet.")


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
