import logging
import uuid
import warnings
from typing import TYPE_CHECKING, Optional

from slixmpp import Iq, Message

from ...slixfix.xep_0490.mds import PUBLISH_OPTIONS
from ...util.types import ChatState, LegacyMessageType, Marker
from .attachment import AttachmentMixin
from .message_maker import MessageMaker
from .message_text import TextMessageMixin

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


class ContentMessageMixin(AttachmentMixin, TextMessageMixin):
    pass


class CarbonMessageMixin(ContentMessageMixin, MarkerMixin):
    def _privileged_send(self, msg: Message):
        i = msg.get_id()
        if i:
            self.session.ignore_messages.add(i)
        else:
            i = "slidge-carbon-" + str(uuid.uuid4())
            msg.set_id(i)
        msg.del_origin_id()
        try:
            self.xmpp["xep_0356"].send_privileged_message(msg)
        except PermissionError:
            try:
                self.xmpp["xep_0356_old"].send_privileged_message(msg)
            except PermissionError:
                warnings.warn(
                    "Slidge does not have privileges to send message on behalf of"
                    " user.Refer to"
                    " https://slidge.im/core/admin/privilege.html"
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
