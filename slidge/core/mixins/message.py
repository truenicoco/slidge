import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import IO, Iterable, Optional, Union

import aiohttp
from slixmpp import JID, Message
from slixmpp.plugins.xep_0363 import FileUploadError
from slixmpp.types import MessageTypes

from slidge.core import config
from slidge.util.types import LegacyMessageType

from ...util.types import ChatState, Marker, ProcessingHint
from .base import BaseSender


class MessageMaker(BaseSender):
    mtype: MessageTypes = NotImplemented
    STRIP_SHORT_DELAY = False
    USE_STANZA_ID = False
    _is_composing = False

    def _make_message(
        self,
        state: Optional[ChatState] = None,
        hints: Iterable[ProcessingHint] = (),
        legacy_msg_id: Optional[LegacyMessageType] = None,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        carbon=False,
        **kwargs,
    ):
        body = kwargs.pop("mbody", None)
        mfrom = kwargs.pop("mfrom", self.jid)
        mto = kwargs.pop("mto", None)
        if carbon:
            # the msg needs to have jabber:client as xmlns, so
            # we don't want to associate with the XML stream
            msg_cls = Message  # type:ignore
        else:
            msg_cls = self.xmpp.Message  # type:ignore
        msg = msg_cls(sfrom=mfrom, stype=self.mtype, sto=mto, **kwargs)
        if body:
            if self._is_composing:
                state = "active"
                self._is_composing = False
            msg["body"] = body
        if state:
            self._is_composing = state == "composing"
            msg["chat_state"] = state
        for hint in hints:
            msg.enable(hint)
        self._set_msg_id(msg, legacy_msg_id)
        self._add_delay(msg, when)
        self._add_reply_to(msg, reply_to_msg_id, reply_to_fallback_text, reply_to_jid)
        return msg

    def _set_msg_id(
        self, msg: Message, legacy_msg_id: Optional[LegacyMessageType] = None
    ):
        if legacy_msg_id is not None:
            i = self._legacy_to_xmpp(legacy_msg_id)
            msg.set_id(i)
            if self.USE_STANZA_ID:
                msg["stanza_id"]["id"] = i
                msg["stanza_id"]["by"] = self.muc.jid  # type: ignore

    def _legacy_to_xmpp(self, legacy_id: LegacyMessageType):
        return self.session.sent.get(
            legacy_id
        ) or self.session.legacy_msg_id_to_xmpp_msg_id(legacy_id)

    def _add_delay(self, msg: Message, when: Optional[datetime]):
        if when:
            if when.tzinfo is None:
                when = when.astimezone(timezone.utc)
            if self.STRIP_SHORT_DELAY:
                delay = datetime.now().astimezone(timezone.utc) - when
                if delay < config.IGNORE_DELAY_THRESHOLD:
                    return
            msg["delay"].set_stamp(when)
            msg["delay"].set_from(self.xmpp.boundjid.bare)

    def _add_reply_to(
        self,
        msg: Message,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_author: Optional[JID] = None,
    ):
        if reply_to_msg_id is not None:
            xmpp_id = self._legacy_to_xmpp(reply_to_msg_id)
            msg["reply"]["id"] = xmpp_id
            # FIXME: https://xmpp.org/extensions/xep-0461.html#usecases mentions that a full JID must be used here
            if reply_to_author:
                msg["reply"]["to"] = reply_to_author
            if reply_to_fallback_text:
                msg["feature_fallback"].add_quoted_fallback(reply_to_fallback_text)


class ChatStateMixin(MessageMaker):
    def _chat_state(self, state: ChatState, **kwargs):
        msg = self._make_message(
            state=state, hints={"no-store"}, carbon=kwargs.get("carbon")
        )
        self._send(msg, **kwargs)

    def active(self, **kwargs):
        """
        Send an "active" chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("active", **kwargs)

    def composing(self, **kwargs):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("composing", **kwargs)

    def paused(self, **kwargs):
        """
        Send a "paused" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("paused", **kwargs)

    def inactive(self, **kwargs):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("inactive", **kwargs)

    def gone(self, **kwargs):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("gone", **kwargs)


class MarkerMixin(MessageMaker):
    is_group: bool = NotImplemented

    def _make_marker(
        self, legacy_msg_id: LegacyMessageType, marker: Marker, carbon=False
    ):
        msg = self._make_message(carbon=carbon)
        msg[marker]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        return msg

    def _make_receipt(self, legacy_msg_id: LegacyMessageType, carbon=False):
        msg = self._make_message(carbon=carbon)
        msg["receipt"] = self._legacy_to_xmpp(legacy_msg_id)
        return msg

    def ack(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(
            self._make_marker(
                legacy_msg_id, "acknowledged", carbon=kwargs.get("carbon")
            ),
            **kwargs,
        )

    def received(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send a "received" message marker (:xep:`0333`) and a "message delivery receipt"
        (:xep:`0184`)
        from this contact to the user

        :param legacy_msg_id: The message this marker refers to
        """
        carbon = kwargs.get("carbon")
        if not self.is_group:
            # msg receipts are NOT RECOMMENDED for MUCs
            self._send(self._make_receipt(legacy_msg_id, carbon=carbon), **kwargs)
        self._send(
            self._make_marker(legacy_msg_id, "received", carbon=carbon), **kwargs
        )

    def displayed(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(
            self._make_marker(legacy_msg_id, "displayed", carbon=kwargs.get("carbon")),
            **kwargs,
        )


class ContentMessageMixin(MessageMaker):
    async def _upload(
        self,
        filename: Union[Path, str],
        content_type: Optional[str] = None,
        input_file: Optional[IO[bytes]] = None,
        url: Optional[str] = None,
    ):
        if url is not None:
            if input_file is not None:
                raise TypeError("Either a URL or a file-like object")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as r:
                    input_file = BytesIO(await r.read())
        try:
            return await self.xmpp["xep_0363"].upload_file(
                filename=filename,
                content_type=content_type,
                input_file=input_file,
                ifrom=config.UPLOAD_REQUESTER or self.xmpp.boundjid,
            )
        except FileUploadError as e:
            log.warning(
                "Something is wrong with the upload service, see the traceback below"
            )
            log.exception(e)

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        **kwargs,
    ):
        """
        Transmit a message from the entity to the user

        :param body: Context of the message
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_to_jid: JID of the quoted message author
        """
        msg = self._make_message(
            mbody=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=reply_to_jid,
            hints=kwargs.get("hints") or {"markable", "store"},
            carbon=kwargs.get("carbon"),
        )
        self._send(msg, **kwargs)

    async def send_file(
        self,
        filename: Union[Path, str],
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        content_type: Optional[str] = None,
        input_file: Optional[IO[bytes]] = None,
        url: Optional[str] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to_jid: Optional[JID] = None,
        when: Optional[datetime] = None,
        caption: Optional[str] = None,
        **kwargs,
    ):
        """
        Send a file using HTTP upload (:xep:`0363`)

        :param filename: Filename to use or location on disk to the file to upload
        :param content_type: MIME type, inferred from filename if not given
        :param input_file: Optionally, a file like object instead of a file on disk.
            filename will still be used to give the uploaded file a name
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param url: Optionally, a URL of a file that slidge will download and upload to the
            default file upload service on the xmpp server it's running on. url and input_file
            are mutually exclusive.
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_to_jid: JID of the quoted message author
        :param when: when the file was sent, for a "delay" tag (:xep:`0203`)
        :param caption: an optional text that is linked to the file
        """
        carbon = kwargs.pop("carbon", False)
        msg = self._make_message(
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=reply_to_jid,
            carbon=carbon,
        )
        uploaded_url = await self._upload(filename, content_type, input_file, url)
        if uploaded_url is None:
            if url is not None:
                uploaded_url = url
            else:
                msg["body"] = (
                    "I tried to send a file, but something went wrong. "
                    "Tell your XMPP admin to check slidge logs."
                )
                self._set_msg_id(msg, legacy_msg_id)
                self._send(msg, **kwargs)
                return

        msg["oob"]["url"] = uploaded_url
        msg["body"] = uploaded_url
        if caption:
            self._send(msg, carbon=carbon, **kwargs)
            self.send_text(
                caption, legacy_msg_id=legacy_msg_id, when=when, carbon=carbon, **kwargs
            )
        else:
            self._set_msg_id(msg, legacy_msg_id)
            self._send(msg, **kwargs)

    def correct(self, legacy_msg_id: LegacyMessageType, new_text: str, **kwargs):
        """
        Call this when a legacy contact has modified his last message content.

        Uses last message correction (:xep:`0308`)

        :param legacy_msg_id: Legacy message ID this correction refers to
        :param new_text: The new text
        """
        msg = self._make_message(mbody=new_text, carbon=kwargs.get("carbon"))
        msg["replace"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        self._send(msg, **kwargs)

    def react(
        self, legacy_msg_id: LegacyMessageType, emojis: Iterable[str] = (), **kwargs
    ):
        """
        Call this when a legacy contact reacts to a message

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: A iterable of emojis used as reactions
        :return:
        """
        msg = self._make_message(hints={"store"}, carbon=kwargs.get("carbon"))
        xmpp_id = self._legacy_to_xmpp(legacy_msg_id)
        self.xmpp["xep_0444"].set_reactions(msg, to_id=xmpp_id, reactions=emojis)
        self._send(msg, **kwargs)

    def retract(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Call this when a legacy contact retracts (:XEP:`0424`) a message

        :param legacy_msg_id: Legacy ID of the message to delete
        """
        msg = self._make_message(
            state=None,
            hints={"store"},
            mbody=f"I have deleted the message {legacy_msg_id}, "
            "but your XMPP client does not support that",
            carbon=kwargs.get("carbon"),
        )
        msg.enable("fallback")
        msg["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        msg["apply_to"].enable("retract")
        self._send(msg, **kwargs)


class CarbonMessageMixin(ContentMessageMixin, MarkerMixin):
    def _privileged_send(self, msg: Message):
        self.session.ignore_messages.add(msg.get_id())
        try:
            self.xmpp["xep_0356"].send_privileged_message(msg)
        except PermissionError:
            try:
                self.xmpp["xep_0356_old"].send_privileged_message(msg)
            except PermissionError:
                log.warning(
                    "Slidge does not have privileges to send message on behalf of user."
                    "Refer to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html "
                    "for more info."
                )


class MessageMixin(ChatStateMixin, MarkerMixin, ContentMessageMixin):
    pass


class MessageCarbonMixin(ChatStateMixin, CarbonMessageMixin):
    pass


log = logging.getLogger(__name__)
