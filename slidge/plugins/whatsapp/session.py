from asyncio import iscoroutine, run_coroutine_threadsafe
from datetime import datetime
from functools import wraps
from io import BytesIO
from mimetypes import guess_type
from os.path import basename
from typing import Any, Optional

from slidge import BaseSession, GatewayUser, LegacyContact, user_store
from slidge.plugins.whatsapp.generated import whatsapp, go
from .config import Config
from .gateway import Gateway
from .contact import Contact, Roster

MESSAGE_PAIR_SUCCESS = (
    "Pairing successful! You might need to repeat this process in the future if the Linked Device is "
    "re-registered from your main device."
)

MESSAGE_LOGGED_OUT = "You have been logged out, please re-scan the QR code on your main device to log in."


class Session(BaseSession[Contact, Roster, Gateway]):
    def __init__(self, user: GatewayUser):
        super().__init__(user)
        self.whatsapp = self.xmpp.whatsapp.Session(
            whatsapp.LinkedDevice(ID=self.user.registration_form.get("device_id", ""))
        )
        self._handle_event = make_sync(self.handle_event, self.xmpp.loop)
        self.whatsapp.SetEventHandler(self._handle_event)

    async def login(self):
        """
        Initiate login process and connect session to WhatsApp. Depending on existing state, login
        might either return having initiated the Linked Device registration process in the background,
        or will re-connect to a previously existing Linked Device session.
        """
        self.whatsapp.Login()

    async def logout(self):
        """
        Logout from the active WhatsApp session. This will also force a remote log-out, and thus
        require pairing on next login. For simply disconnecting the active session, look at the
        :meth:`.Session.disconnect` function.
        """
        self.whatsapp.Logout()

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
        elif event == whatsapp.EventPairSuccess:
            self.send_gateway_message(MESSAGE_PAIR_SUCCESS)
            self.user.registration_form["device_id"] = data.PairDeviceID
            user_store.add(self.user.jid, self.user.registration_form)
            self.whatsapp.FetchRoster(refresh=True)
        elif event == whatsapp.EventConnected:
            self.send_gateway_status("Logged in")
            self.whatsapp.FetchRoster(refresh=Config.ALWAYS_SYNC_ROSTER)
        elif event == whatsapp.EventLoggedOut:
            self.send_gateway_message(MESSAGE_LOGGED_OUT)
            self.send_gateway_status("Logged out", show="away")
            await self.login()
        elif event == whatsapp.EventContactSync:
            contact = self.contacts.by_legacy_id(data.Contact.JID)
            contact.name = data.Contact.Name
            if data.Contact.AvatarURL != "":
                contact.avatar = data.Contact.AvatarURL
            await contact.add_to_roster()
        elif event == whatsapp.EventPresence:
            self.contacts.by_legacy_id(data.Presence.JID).update_presence(
                data.Presence.Away, data.Presence.LastSeen
            )
        elif event == whatsapp.EventChatState:
            contact = self.contacts.by_legacy_id(data.ChatState.JID)
            if data.ChatState.Kind == whatsapp.ChatStateComposing:
                contact.composing()
            elif data.ChatState.Kind == whatsapp.ChatStatePaused:
                contact.paused()
        elif event == whatsapp.EventReceipt:
            await self.handle_receipt(data.Receipt)
        elif event == whatsapp.EventMessage:
            await self.handle_message(data.Message)

    async def handle_receipt(self, receipt: whatsapp.Receipt):
        """
        Handle incoming delivered/read receipt, as propagated by the WhatsApp adapter.
        """
        contact = self.contacts.by_legacy_id(receipt.JID)
        for message_id in receipt.MessageIDs:
            if receipt.IsCarbon:
                message_timestamp = datetime.fromtimestamp(receipt.Timestamp)
                contact.carbon_read(legacy_msg_id=message_id, when=message_timestamp)
            elif receipt.Kind == whatsapp.ReceiptDelivered:
                contact.received(message_id)
            elif receipt.Kind == whatsapp.ReceiptRead:
                contact.displayed(message_id)

    async def handle_message(self, message: whatsapp.Message):
        """
        Handle incoming message, as propagated by the WhatsApp adapter. Messages can be one of many
        types, including plain-text messages, media messages, reactions, etc., and may also include
        other aspects such as references to other messages for the purposes of quoting or correction.
        """
        contact = self.contacts.by_legacy_id(message.JID)
        message_reply_id = message.ReplyID if message.ReplyID != "" else None
        message_reply_body = message.ReplyBody if message.ReplyBody != "" else None
        message_timestamp = (
            datetime.fromtimestamp(message.Timestamp) if message.Timestamp > 0 else None
        )
        if message.IsCarbon:
            if message.Kind == whatsapp.MessagePlain:
                contact.carbon(
                    body=message.Body,
                    legacy_id=message.ID,
                    when=message_timestamp,
                    reply_to_msg_id=message_reply_id,
                    reply_to_fallback_text=message_reply_body,
                )
            elif message.Kind == whatsapp.MessageAttachment:
                for ptr in message.Attachments:
                    attachment = whatsapp.Attachment(handle=ptr)
                    attachment_caption = (
                        attachment.Caption if attachment.Caption != "" else None
                    )
                    await contact.carbon_upload(
                        filename=attachment.Filename,
                        content_type=attachment.MIME,
                        input_file=BytesIO(initial_bytes=bytes(attachment.Data)),
                        legacy_id=message.ID,
                        reply_to_msg_id=message_reply_id,
                        when=message_timestamp,
                    )
            elif message.Kind == whatsapp.MessageRevoke:
                contact.carbon_retract(legacy_msg_id=message.ID, when=message_timestamp)
            elif message.Kind == whatsapp.MessageReaction:
                contact.carbon_react(
                    legacy_msg_id=message.ID,
                    reactions=message.Body,
                    when=message_timestamp,
                )
        elif message.Kind == whatsapp.MessagePlain:
            contact.send_text(
                body=message.Body,
                legacy_msg_id=message.ID,
                when=message_timestamp,
                reply_to_msg_id=message_reply_id,
                reply_to_fallback_text=message_reply_body,
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
                )
        elif message.Kind == whatsapp.MessageRevoke:
            contact.retract(message.ID)
        elif message.Kind == whatsapp.MessageReaction:
            contact.react(legacy_msg_id=message.ID, emojis=message.Body)

    async def send_text(
        self,
        t: str,
        c: LegacyContact,
        *,
        reply_to_msg_id: Optional[str] = None,
        reply_to_fallback_text: Optional[str] = None,
    ):
        """
        Send outgoing plain-text message to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message = whatsapp.Message(ID=message_id, JID=c.legacy_id, Body=t)
        if reply_to_msg_id is not None:
            message.ReplyID = reply_to_msg_id
        if reply_to_fallback_text is not None:
            message.ReplyBody = strip_quote_prefix(reply_to_fallback_text)
            message.Body = message.Body.lstrip()
        self.whatsapp.SendMessage(message)
        return message_id

    async def send_file(
        self,
        u: str,
        c: LegacyContact,
        *,
        reply_to_msg_id: Optional[str] = None,
    ):
        """
        Send outgoing media message (i.e. audio, image, document) to given WhatsApp contact.
        """
        message_id = whatsapp.GenerateMessageID()
        message_attachment = whatsapp.Attachment(
            MIME=guess_type(u)[0], Filename=basename(u), URL=u
        )
        self.whatsapp.SendMessage(
            whatsapp.Message(
                Kind=whatsapp.MessageAttachment,
                ID=message_id,
                JID=c.legacy_id,
                ReplyID=reply_to_msg_id if reply_to_msg_id is not None else "",
                Attachments=whatsapp.Slice_whatsapp_Attachment([message_attachment]),
            )
        )
        return message_id

    async def active(self, c: LegacyContact):
        """
        WhatsApp has no equivalent to the "active" chat state, so calls to this function are no-ops.
        """
        pass

    async def inactive(self, c: LegacyContact):
        """
        WhatsApp has no equivalent to the "inactive" chat state, so calls to this function are no-ops.
        """
        pass

    async def composing(self, c: LegacyContact):
        """
        Send "composing" chat state to given WhatsApp contact, signifying that a message is currently
        being composed.
        """
        self.whatsapp.SendChatState(
            whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStateComposing)
        )

    async def paused(self, c: LegacyContact):
        """
        Send "paused" chat state to given WhatsApp contact, signifying that an (unsent) message is no
        longer being composed.
        """
        self.whatsapp.SendChatState(
            whatsapp.ChatState(JID=c.legacy_id, Kind=whatsapp.ChatStatePaused)
        )

    async def displayed(self, legacy_msg_id: Any, c: LegacyContact):
        """
        Send "read" receipt, signifying that the WhatsApp message sent has been displayed on the XMPP
        client.
        """
        self.whatsapp.SendReceipt(
            whatsapp.Receipt(
                MessageIDs=go.Slice_string([legacy_msg_id]), JID=c.legacy_id
            )
        )

    async def react(self, legacy_msg_id: Any, emojis: list[str], c: LegacyContact):
        """
        Send or remove emoji reaction to existing WhatsApp message. Noted that WhatsApp places
        restrictions on the number of emoji reactions a user can place on any given message; these
        restrictions are currently not observed by this function.
        """
        for emoji in emojis if len(emojis) > 0 else [""]:
            self.whatsapp.SendMessage(
                whatsapp.Message(
                    Kind=whatsapp.MessageReaction,
                    ID=legacy_msg_id,
                    JID=c.legacy_id,
                    Body=emoji,
                    IsCarbon=legacy_msg_id in self.sent,
                )
            )

    async def retract(self, legacy_msg_id: Any, c: LegacyContact):
        """
        Request deletion (aka retraction) for a given WhatsApp message.
        """
        self.whatsapp.SendMessage(
            whatsapp.Message(
                Kind=whatsapp.MessageRevoke, ID=legacy_msg_id, JID=c.legacy_id
            )
        )

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContact):
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
