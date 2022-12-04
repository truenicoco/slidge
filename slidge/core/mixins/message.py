import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import IO, Iterable, Optional, Union

import aiohttp
from slixmpp import Message
from slixmpp.plugins.xep_0363 import FileUploadError
from slixmpp.types import MessageTypes

from slidge.core import config
from slidge.util.types import LegacyMessageType

from ...util.types import ChatState, Marker, ProcessingHint
from .base import BaseSender


class MessageMaker(BaseSender):
    mtype: MessageTypes = NotImplemented

    def _make_message(
        self,
        state: Optional[ChatState] = None,
        hints: Iterable[ProcessingHint] = (),
        **msg_kwargs,
    ):
        body = msg_kwargs.pop("mbody", None)
        mfrom = msg_kwargs.pop("mfrom", self.jid)
        mto = msg_kwargs.pop("mto", None)
        msg = self.xmpp.Message(sfrom=mfrom, stype=self.mtype, sto=mto, **msg_kwargs)
        if body:
            msg["body"] = body
        if state:
            msg["chat_state"] = state
        for hint in hints:
            msg.enable(hint)
        return msg


class ChatStateMixin(MessageMaker):
    def _chat_state(self, state: ChatState):
        msg = self._make_message(state=state, hints={"no-store"})
        self._send(msg)

    def active(self):
        """
        Send an "active" chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("active")

    def composing(self):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("composing")

    def paused(self):
        """
        Send a "paused" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("paused")

    def inactive(self):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("inactive")

    def gone(self):
        """
        Send an "inactive" (ie "typing paused notification") chat state (:xep:`0085`) from this contact to the user.
        """
        self._chat_state("gone")


class MarkerMixin(MessageMaker):
    def _make_marker(
        self, legacy_msg_id: LegacyMessageType, marker: Marker, **msg_kwargs
    ):
        msg = self._make_message(**msg_kwargs)
        msg[marker]["id"] = self.session.sent.get(legacy_msg_id)
        return msg

    def _make_receipt(self, legacy_msg_id: LegacyMessageType):
        msg = self._make_message()
        msg["receipt"] = self.session.sent.get(legacy_msg_id)
        return msg

    def ack(self, legacy_msg_id: LegacyMessageType):
        """
        Send an "acknowledged" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(self._make_marker(legacy_msg_id, "acknowledged"))

    def received(self, legacy_msg_id: LegacyMessageType):
        """
        Send a "received" message marker (:xep:`0333`) and a "message delivery receipt"
        (:xep:`0184`)
        from this contact to the user

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(self._make_receipt(legacy_msg_id))
        self._send(self._make_marker(legacy_msg_id, "received"))

    def displayed(self, legacy_msg_id: LegacyMessageType):
        """
        Send a "displayed" message marker (:xep:`0333`) from this contact to the user.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(self._make_marker(legacy_msg_id, "displayed"))


class ContentMessageMixin(MessageMaker):
    def _legacy_to_xmpp(self, legacy_id: LegacyMessageType):
        return self.session.sent.get(
            legacy_id
        ) or self.session.legacy_msg_id_to_xmpp_msg_id(legacy_id)

    async def __upload(
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

    def _make_bridged_message(
        self,
        state: Optional[ChatState] = "active",
        hints: Iterable[ProcessingHint] = ("markable", "store"),
        legacy_msg_id: Optional[LegacyMessageType] = None,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        **msg_kwargs,
    ):
        msg = self._make_message(state=state, hints=hints, **msg_kwargs)
        if legacy_msg_id is not None:
            msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
        self._add_delay(msg, when)
        self._add_reply_to(msg, reply_to_msg_id, reply_to_fallback_text)
        return msg

    def _add_delay(self, msg: Message, when: Optional[datetime]):
        if when:
            if when.tzinfo is None:
                when = when.astimezone(timezone.utc)
            if (
                datetime.now().astimezone(timezone.utc) - when
                > config.IGNORE_DELAY_THRESHOLD
            ):
                msg["delay"].set_stamp(when)
                msg["delay"].set_from(self.xmpp.boundjid.bare)

    def _add_reply_to(
        self,
        msg: Message,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
    ):
        if reply_to_msg_id is not None:
            xmpp_id = self._legacy_to_xmpp(reply_to_msg_id)
            msg["reply"]["id"] = self.session.legacy_msg_id_to_xmpp_msg_id(xmpp_id)
            # FIXME: https://xmpp.org/extensions/xep-0461.html#usecases mentions that a full JID must be used here
            msg["reply"]["to"] = self.user.jid
            if reply_to_fallback_text:
                msg["feature_fallback"].add_quoted_fallback(reply_to_fallback_text)

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
    ):
        """
        Transmit a message from the contact to the user

        :param body: Context of the message
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param reply_to_msg_id:
        :param reply_to_fallback_text:
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)

        :return: the XMPP message that was sent
        """
        msg = self._make_bridged_message(
            mbody=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
        )
        self._send(msg)

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
        when: Optional[datetime] = None,
        caption: Optional[str] = None,
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
        :param reply_to_msg_id:
        :param reply_to_fallback_text:
        :param when: when the file was sent, for a "delay" tag (:xep:`0203`)
        :param caption: an optional text that is linked to the file

        :return: The msg stanza that was sent
        """
        msg = self._make_bridged_message(
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
        )
        uploaded_url = await self.__upload(filename, content_type, input_file, url)
        if uploaded_url is None:
            if url is not None:
                uploaded_url = url
            else:
                msg["body"] = (
                    "I tried to send a file, but something went wrong. "
                    "Tell your XMPP admin to check slidge logs."
                )
                if legacy_msg_id:
                    msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
                self._send(msg)
                return

        msg["oob"]["url"] = uploaded_url
        msg["body"] = uploaded_url
        if caption:
            self._send(msg)
            self.send_text(caption, legacy_msg_id=legacy_msg_id, when=when)
        else:
            if legacy_msg_id:
                msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
            self._send(msg)

    def correct(self, legacy_msg_id: LegacyMessageType, new_text: str):
        """
        Call this when a legacy contact has modified his last message content.

        Uses last message correction (:xep:`0308`)

        :param legacy_msg_id: Legacy message ID this correction refers to
        :param new_text: The new text
        """
        msg = self._make_bridged_message(mbody=new_text)
        msg["replace"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        self._send(msg)

    def react(self, legacy_msg_id: LegacyMessageType, emojis: Iterable[str] = ()):
        """
        Call this when a legacy contact reacts to a message

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: A iterable of emojis used as reactions
        :return:
        """
        msg = self._make_bridged_message(hints={"store"})
        xmpp_id = self._legacy_to_xmpp(legacy_msg_id)
        self.xmpp["xep_0444"].set_reactions(msg, to_id=xmpp_id, reactions=emojis)
        self._send(msg)

    def retract(self, legacy_msg_id: LegacyMessageType):
        """
        Call this when a legacy contact retracts (:XEP:`0424`) a message

        :param legacy_msg_id: Legacy ID of the message to delete
        """
        msg = self._make_bridged_message(
            state=None,
            hints={"store"},
            mbody=f"I have deleted the message {legacy_msg_id}, "
            "but your XMPP client does not support that",
        )
        msg.enable("fallback")
        msg["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        msg["apply_to"].enable("retract")
        self._send(msg)


class CarbonMessageMixin(ContentMessageMixin, MarkerMixin):
    def _make_message_with_jabber_client_namespace(self, **stanza_kwargs):
        return Message(
            sfrom=self.user.jid, stype=self.mtype, sto=self.jid.bare, **stanza_kwargs
        )

    def _make_carbon(
        self, legacy_msg_id: Optional[LegacyMessageType] = None, **msg_kwargs
    ):
        body = msg_kwargs.pop("mbody", None)
        when = msg_kwargs.pop("when", None)
        reply_to_msg = msg_kwargs.pop("reply_to_msg_id", None)
        reply_to_fallback_text = msg_kwargs.pop("reply_to_fallback_text", None)
        msg = self._make_message_with_jabber_client_namespace(**msg_kwargs)

        if body:
            msg["body"] = body
        msg.enable("store")
        if legacy_msg_id is not None:
            msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
        self._add_delay(msg, when)
        self._add_reply_to(msg, reply_to_msg, reply_to_fallback_text)
        return msg

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
                return
        msg.get_id()

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        carbon=False,
    ):
        if carbon:
            msg = self._make_carbon(
                mbody=body,
                legacy_msg_id=legacy_msg_id,
                when=when,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_to_fallback_text,
            )
            self._privileged_send(msg)
        else:
            super().send_text(
                body,
                legacy_msg_id=legacy_msg_id,
                when=when,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_to_fallback_text,
            )

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
        when: Optional[datetime] = None,
        caption: Optional[str] = None,
        carbon=False,
    ):
        if carbon:
            msg = self._make_carbon(
                when=when,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_to_fallback_text,
            )
            uploaded_url = await self.__upload(filename, content_type, input_file, url)
            if uploaded_url is None:
                if url is not None:
                    uploaded_url = url
                else:
                    msg["body"] = (
                        "I tried to send a file, but something went wrong. "
                        "Tell your XMPP admin to check slidge logs."
                    )
                    if legacy_msg_id:
                        msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
                    self._privileged_send(msg)
                    return

            msg["oob"]["url"] = uploaded_url
            msg["body"] = uploaded_url
            if caption:
                self._privileged_send(msg)
                self.send_text(
                    caption, legacy_msg_id=legacy_msg_id, when=when, carbon=True
                )
            else:
                if legacy_msg_id:
                    msg.set_id(self._legacy_to_xmpp(legacy_msg_id))
                self._privileged_send(msg)
        else:
            await super().send_file(
                filename=filename,
                content_type=content_type,
                input_file=input_file,
                url=url,
                legacy_msg_id=legacy_msg_id,
                reply_to_msg_id=reply_to_msg_id,
                reply_to_fallback_text=reply_to_fallback_text,
                caption=caption,
            )

    def correct(self, legacy_msg_id: LegacyMessageType, new_text: str, *, carbon=False):
        if carbon:
            msg = self._make_carbon(mbody=new_text)
            msg["replace"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
            self._privileged_send(msg)
        else:
            super().correct(legacy_msg_id, new_text)

    def react(
        self,
        legacy_msg_id: LegacyMessageType,
        emojis: Iterable[str] = (),
        *,
        carbon=False,
    ):
        if carbon:
            xmpp_id = self._legacy_to_xmpp(legacy_msg_id)
            msg = self._make_carbon()
            self.xmpp["xep_0444"].set_reactions(msg, to_id=xmpp_id, reactions=emojis)
            self._privileged_send(msg)
        else:
            super().react(legacy_msg_id, emojis)

    def retract(self, legacy_msg_id: LegacyMessageType, *, carbon=False):
        if carbon:
            msg = self._make_carbon(
                mbody=f"I have deleted the message {legacy_msg_id}, "
                "but your XMPP client does not support that"
            )
            msg.enable("fallback")
            msg["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
            msg["apply_to"].enable("retract")
            self._privileged_send(msg)
        else:
            super().retract(legacy_msg_id)

    def displayed(self, legacy_msg_id: LegacyMessageType, *, carbon=False):
        if carbon:
            msg = self._make_message_with_jabber_client_namespace()
            msg["from"] = self.user.jid.bare
            self._privileged_send(msg)
        else:
            super().displayed(legacy_msg_id)


class MessageMixin(ChatStateMixin, MarkerMixin, ContentMessageMixin):
    pass


class MessageCarbonMixin(ChatStateMixin, CarbonMessageMixin):
    pass


log = logging.getLogger(__name__)
