import logging
from datetime import datetime
from typing import Iterable, Optional

from ...util.types import (
    LegacyMessageType,
    LegacyThreadType,
    LinkPreview,
    MessageReference,
    ProcessingHint,
)
from .message_maker import MessageMaker


class TextMessageMixin(MessageMaker):
    def __default_hints(self, hints: Optional[Iterable[ProcessingHint]] = None):
        if hints is not None:
            return hints
        elif self.mtype == "chat":
            return {"markable", "store"}
        elif self.mtype == "groupchat":
            return {"markable"}

    def _replace_id(self, legacy_msg_id: LegacyMessageType):
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
        if carbon and not hasattr(self, "muc"):
            if not correction and self.xmpp.store.sent.was_sent_by_user(
                self.session.user_pk, str(legacy_msg_id)
            ):
                log.warning(
                    "Carbon message for a message an XMPP has sent? This is a bug! %s",
                    legacy_msg_id,
                )
                return
            if hasattr(self, "muc") and not self.is_user:  # type:ignore
                log.warning(
                    "send_text() called with carbon=True on a participant who is not the user",
                    legacy_msg_id,
                )
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
            msg["replace"]["id"] = self._replace_id(legacy_msg_id)
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
        msg["retract"]["id"] = msg["replace"]["id"] = self._replace_id(legacy_msg_id)
        self._send(msg, **kwargs)


log = logging.getLogger(__name__)
