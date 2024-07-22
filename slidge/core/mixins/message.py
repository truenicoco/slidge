import logging
import uuid
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Optional

from slixmpp import Iq, Message

from ...slixfix.xep_0490.mds import PUBLISH_OPTIONS
from ...util.types import (
    ChatState,
    LegacyMessageType,
    LegacyThreadType,
    LinkPreview,
    Marker,
    MessageReference,
    ProcessingHint,
)
from .attachment import AttachmentMixin
from .message_maker import MessageMaker

if TYPE_CHECKING:
    from ...group import LegacyMUC


class ChatStateMixin(MessageMaker):
    def __init__(self):
        super().__init__()
        self.__last_chat_state: Optional[ChatState] = None

    def _chat_state(self, state: ChatState, forced=False, **kwargs):
        carbon = kwargs.get("carbon", False)
        if carbon or (state == self.__last_chat_state and not forced):
            return
        self.__last_chat_state = state
        msg = self._make_message(state=state, hints={"no-store"})
        self._send(msg, **kwargs)

    def active(self, **kwargs):
        """
        Send an "active" chat state (:xep:`0085`) from this
        :term:`XMPP Entity`.
        """
        self._chat_state("active", **kwargs)

    def composing(self, **kwargs):
        """
        Send a "composing" (ie "typing notification") chat state (:xep:`0085`)
        from this :term:`XMPP Entity`.
        """
        self._chat_state("composing", forced=True, **kwargs)

    def paused(self, **kwargs):
        """
        Send a "paused" (ie "typing paused notification") chat state
        (:xep:`0085`) from this :term:`XMPP Entity`.
        """
        self._chat_state("paused", **kwargs)

    def inactive(self, **kwargs):
        """
        Send an "inactive" (ie "contact has not interacted with the chat session
        interface for an intermediate period of time") chat state (:xep:`0085`)
        from this :term:`XMPP Entity`.
        """
        self._chat_state("inactive", **kwargs)

    def gone(self, **kwargs):
        """
        Send a "gone" (ie "contact has not interacted with the chat session interface,
        system, or device for a relatively long period of time") chat state
        (:xep:`0085`) from this :term:`XMPP Entity`.
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
        Send an "acknowledged" message marker (:xep:`0333`) from this :term:`XMPP Entity`.

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
        Send a "received" message marker (:xep:`0333`) from this :term:`XMPP Entity`.
        If called on a :class:`LegacyContact`, also send a delivery receipt
        marker (:xep:`0184`).

        :param legacy_msg_id: The message this marker refers to
        """
        carbon = kwargs.get("carbon")
        if self.mtype == "chat":
            self._send(
                self.xmpp.delivery_receipt.make_ack(
                    self._legacy_to_xmpp(legacy_msg_id),
                    mfrom=self.jid,
                    mto=self.user_jid,
                )
            )
        self._send(
            self._make_marker(legacy_msg_id, "received", carbon=carbon), **kwargs
        )

    def displayed(self, legacy_msg_id: LegacyMessageType, **kwargs):
        """
        Send a "displayed" message marker (:xep:`0333`) from this :term:`XMPP Entity`.

        :param legacy_msg_id: The message this marker refers to
        """
        self._send(
            self._make_marker(legacy_msg_id, "displayed", carbon=kwargs.get("carbon")),
            **kwargs,
        )
        if getattr(self, "is_user", False):
            self.session.create_task(self.__send_mds(legacy_msg_id))

    async def __send_mds(self, legacy_msg_id: LegacyMessageType):
        # Send a MDS displayed marker on behalf of the user for a group chat
        if muc := getattr(self, "muc", None):
            muc_jid = muc.jid.bare
        else:
            # This is not implemented for 1:1 chat because it would rely on
            # storing the XMPP-server injected stanza-id, which we don't track
            # ATM.
            # In practice, MDS should mostly be useful for public group chats,
            # so it should not be an issue.
            # We'll see if we need to implement that later
            return
        xmpp_msg_id = self._legacy_to_xmpp(legacy_msg_id)
        iq = Iq(sto=self.user_jid.bare, sfrom=self.user_jid.bare, stype="set")
        iq["pubsub"]["publish"]["node"] = self.xmpp["xep_0490"].stanza.NS
        iq["pubsub"]["publish"]["item"]["id"] = muc_jid
        displayed = self.xmpp["xep_0490"].stanza.Displayed()
        displayed["stanza_id"]["id"] = xmpp_msg_id
        displayed["stanza_id"]["by"] = muc_jid
        iq["pubsub"]["publish"]["item"]["payload"] = displayed
        iq["pubsub"]["publish_options"] = PUBLISH_OPTIONS
        try:
            await self.xmpp["xep_0356"].send_privileged_iq(iq)
        except Exception as e:
            self.session.log.debug("Could not MDS mark", exc_info=e)


class ContentMessageMixin(AttachmentMixin):
    def __default_hints(self, hints: Optional[Iterable[ProcessingHint]] = None):
        if hints is not None:
            return hints
        elif self.mtype == "chat":
            return {"markable", "store"}
        elif self.mtype == "groupchat":
            return {"markable"}

    def __replace_id(self, legacy_msg_id: LegacyMessageType):
        if self.mtype == "groupchat":
            return self.xmpp.store.sent.get_group_xmpp_id(
                self.session.user_pk, str(legacy_msg_id)
            ) or self._legacy_to_xmpp(legacy_msg_id)
        else:
            return self._legacy_to_xmpp(legacy_msg_id)

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to: Optional[MessageReference] = None,
        thread: Optional[LegacyThreadType] = None,
        hints: Optional[Iterable[ProcessingHint]] = None,
        carbon=False,
        archive_only=False,
        correction=False,
        correction_event_id: Optional[LegacyMessageType] = None,
        link_previews: Optional[list[LinkPreview]] = None,
        **send_kwargs,
    ):
        """
        Send a text message from this :term:`XMPP Entity`.

        :param body: Content of the message
        :param legacy_msg_id: If you want to be able to transport read markers from the gateway
            user to the legacy network, specify this
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)
        :param reply_to: Quote another message (:xep:`0461`)
        :param hints:
        :param thread:
        :param carbon: (only used if called on a :class:`LegacyContact`)
            Set this to ``True`` if this is actually a message sent **to** the
            :class:`LegacyContact` by the :term:`User`.
            Use this to synchronize outgoing history for legacy official apps.
        :param correction: whether this message is a correction or not
        :param correction_event_id: in the case where an ID is associated with the legacy
            'correction event', specify it here to use it on the XMPP side. If not specified,
            a random ID will be used.
        :param link_previews: A little of sender (or server, or gateway)-generated
            previews of URLs linked in the body.
        :param archive_only: (only in groups) Do not send this message to user,
            but store it in the archive. Meant to be used during ``MUC.backfill()``
        """
        if carbon:
            if not correction and self.xmpp.store.sent.was_sent_by_user(
                self.session.user_pk, str(legacy_msg_id)
            ):
                log.warning(
                    "Carbon message for a message an XMPP has sent? This is a bug! %s",
                    legacy_msg_id,
                )
                return
            self.xmpp.store.sent.set_message(
                self.session.user_pk,
                str(legacy_msg_id),
                self.session.legacy_to_xmpp_msg_id(legacy_msg_id),
            )
        hints = self.__default_hints(hints)
        msg = self._make_message(
            mbody=body,
            legacy_msg_id=correction_event_id if correction else legacy_msg_id,
            when=when,
            reply_to=reply_to,
            hints=hints or (),
            carbon=carbon,
            thread=thread,
            link_previews=link_previews,
        )
        if correction:
            msg["replace"]["id"] = self.__replace_id(legacy_msg_id)
        return self._send(
            msg,
            archive_only=archive_only,
            carbon=carbon,
            legacy_msg_id=legacy_msg_id,
            **send_kwargs,
        )

    def correct(
        self,
        legacy_msg_id: LegacyMessageType,
        new_text: str,
        *,
        when: Optional[datetime] = None,
        reply_to: Optional[MessageReference] = None,
        thread: Optional[LegacyThreadType] = None,
        hints: Optional[Iterable[ProcessingHint]] = None,
        carbon=False,
        archive_only=False,
        correction_event_id: Optional[LegacyMessageType] = None,
        link_previews: Optional[list[LinkPreview]] = None,
        **send_kwargs,
    ):
        """
        Modify a message that was previously sent by this :term:`XMPP Entity`.

        Uses last message correction (:xep:`0308`)

        :param new_text: New content of the message
        :param legacy_msg_id: The legacy message ID of the message to correct
        :param when: when the message was sent, for a "delay" tag (:xep:`0203`)
        :param reply_to: Quote another message (:xep:`0461`)
        :param hints:
        :param thread:
        :param carbon: (only in 1:1) Reflect a message sent to this ``Contact`` by the user.
            Use this to synchronize outgoing history for legacy official apps.
        :param archive_only: (only in groups) Do not send this message to user,
            but store it in the archive. Meant to be used during ``MUC.backfill()``
        :param correction_event_id: in the case where an ID is associated with the legacy
            'correction event', specify it here to use it on the XMPP side. If not specified,
            a random ID will be used.
        :param link_previews: A little of sender (or server, or gateway)-generated
            previews of URLs linked in the body.
        """
        self.send_text(
            new_text,
            legacy_msg_id,
            when=when,
            reply_to=reply_to,
            hints=hints,
            carbon=carbon,
            thread=thread,
            correction=True,
            archive_only=archive_only,
            correction_event_id=correction_event_id,
            link_previews=link_previews,
            **send_kwargs,
        )

    def react(
        self,
        legacy_msg_id: LegacyMessageType,
        emojis: Iterable[str] = (),
        thread: Optional[LegacyThreadType] = None,
        **kwargs,
    ):
        """
        Send a reaction (:xep:`0444`) from this :term:`XMPP Entity`.

        :param legacy_msg_id: The message which the reaction refers to.
        :param emojis: An iterable of emojis used as reactions
        :param thread:
        """
        msg = self._make_message(
            hints={"store"}, carbon=kwargs.get("carbon"), thread=thread
        )
        xmpp_id = kwargs.pop("xmpp_id", None)
        if not xmpp_id:
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
        Send a message retraction (:XEP:`0424`) from this :term:`XMPP Entity`.

        :param legacy_msg_id: Legacy ID of the message to delete
        :param thread:
        """
        msg = self._make_message(
            state=None,
            hints={"store"},
            mbody=f"/me retracted the message {legacy_msg_id}",
            carbon=kwargs.get("carbon"),
            thread=thread,
        )
        msg.enable("fallback")
        # namespace version mismatch between slidge and slixmpp, update me later
        msg["fallback"]["for"] = self.xmpp["xep_0424"].namespace[:-1] + "1"
        msg["retract"]["id"] = msg["replace"]["id"] = self.__replace_id(legacy_msg_id)
        self._send(msg, **kwargs)


class CarbonMessageMixin(ContentMessageMixin, MarkerMixin):
    def _privileged_send(self, msg: Message):
        i = msg.get_id()
        if not i:
            i = str(uuid.uuid4())
            msg.set_id(i)
        msg.del_origin_id()
        self.session.ignore_messages.add(i)
        try:
            self.xmpp["xep_0356"].send_privileged_message(msg)
        except PermissionError:
            try:
                self.xmpp["xep_0356_old"].send_privileged_message(msg)
            except PermissionError:
                warnings.warn(
                    "Slidge does not have privileges to send message on behalf of"
                    " user.Refer to"
                    " https://slidge.readthedocs.io/en/latest/admin/xmpp_server.html"
                    " for more info."
                )


class InviteMixin(MessageMaker):
    def invite_to(
        self,
        muc: "LegacyMUC",
        reason: Optional[str] = None,
        password: Optional[str] = None,
        **send_kwargs,
    ):
        """
        Send an invitation to join a group (:xep:`0249`) from this :term:`XMPP Entity`.

        :param muc: the muc the user is invited to
        :param reason: a text explaining why the user should join this muc
        :param password: maybe this will make sense later? not sure
        :param send_kwargs: additional kwargs to be passed to _send()
            (internal use by slidge)
        """
        msg = self._make_message(mtype="normal")
        msg["groupchat_invite"]["jid"] = muc.jid
        if reason:
            msg["groupchat_invite"]["reason"] = reason
        if password:
            msg["groupchat_invite"]["password"] = password
        self._send(msg, **send_kwargs)


class MessageMixin(InviteMixin, ChatStateMixin, MarkerMixin, ContentMessageMixin):
    pass


class MessageCarbonMixin(InviteMixin, ChatStateMixin, CarbonMessageMixin):
    pass


log = logging.getLogger(__name__)
