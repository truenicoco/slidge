import logging
import warnings
from datetime import datetime
from typing import Iterable, Optional

from slixmpp import Message

from ...util.types import (
    ChatState,
    LegacyMessageType,
    LegacyThreadType,
    Marker,
    MessageReference,
    ProcessingHint,
)
from .attachment import AttachmentMixin
from .message_maker import MessageMaker


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
        Send a "received" message marker (:xep:`0333`) from this contact to the user.
        For LegacyContacts, also send a delivery receipt marker (:xep:`0184`)

        :param legacy_msg_id: The message this marker refers to
        """
        carbon = kwargs.get("carbon")
        if self.mtype == "chat":
            self._send(
                self.xmpp.delivery_receipt.make_ack(
                    self._legacy_to_xmpp(legacy_msg_id),
                    mfrom=self.jid,
                    mto=self.user.jid,
                )
            )
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


class ContentMessageMixin(AttachmentMixin):
    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to: Optional[MessageReference] = None,
        thread: Optional[LegacyThreadType] = None,
        hints: Optional[Iterable[ProcessingHint]] = None,
        **kwargs,
    ):
        """
        Transmit a message from the entity to the user

        :param body: Context of the message
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)
        :param reply_to: Quote another message (:xep:`0461`)
        :param thread:
        :param carbon: (only in 1:1) Reflect a message sent to this ``Contact`` by the user.
            Use this to synchronize outgoing history for legacy official apps.
        :param archive_only: (only in groups) Do not send this message to user,
            but store it in the archive. Meant to be used during ``MUC.backfill()``
        """
        carbon = kwargs.get("carbon")
        if carbon:
            self.session.sent[
                legacy_msg_id
            ] = self.session.legacy_msg_id_to_xmpp_msg_id(legacy_msg_id)
        if hints is None:
            if self.mtype == "chat":
                hints = {"markable", "store"}
            elif self.mtype == "groupchat":
                hints = {"markable"}
        msg = self._make_message(
            mbody=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to=reply_to,
            hints=hints or (),
            carbon=carbon,
            thread=thread,
        )
        self._send(msg, **kwargs)

    def correct(
        self,
        legacy_msg_id: LegacyMessageType,
        new_text: str,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact has modified his last message content.

        Uses last message correction (:xep:`0308`)

        :param legacy_msg_id: Legacy message ID this correction refers to
        :param new_text: The new text
        :param thread:
        """
        msg = self._make_message(
            mbody=new_text, carbon=kwargs.get("carbon"), thread=thread
        )
        msg["replace"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        self._send(msg, **kwargs)

    def react(
        self,
        legacy_msg_id: LegacyMessageType,
        emojis: Iterable[str] = (),
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact reacts to a message

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: An iterable of emojis used as reactions
        :param thread:
        """
        msg = self._make_message(
            hints={"store"}, carbon=kwargs.get("carbon"), thread=thread
        )
        xmpp_id = self._legacy_to_xmpp(legacy_msg_id)
        self.xmpp["xep_0444"].set_reactions(msg, to_id=xmpp_id, reactions=emojis)
        self._send(msg, **kwargs)

    def retract(
        self,
        legacy_msg_id: LegacyMessageType,
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Call this when a legacy contact retracts (:XEP:`0424`) a message

        :param legacy_msg_id: Legacy ID of the message to delete
        :param thread:
        """
        msg = self._make_message(
            state=None,
            hints={"store"},
            mbody=f"I have deleted the message {legacy_msg_id}, "
            "but your XMPP client does not support that",
            carbon=kwargs.get("carbon"),
            thread=thread,
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
                warnings.warn(
                    "Slidge does not have privileges to send message on behalf of user."
                    "Refer to https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html "
                    "for more info."
                )


class MessageMixin(ChatStateMixin, MarkerMixin, ContentMessageMixin):
    pass


class MessageCarbonMixin(ChatStateMixin, CarbonMessageMixin):
    pass


log = logging.getLogger(__name__)
