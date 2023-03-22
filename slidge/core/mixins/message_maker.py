from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import uuid4

from slixmpp import JID, Message
from slixmpp.types import MessageTypes

from ...util.types import ChatState, LegacyMessageType, ProcessingHint
from .. import config
from .base import BaseSender


class MessageMaker(BaseSender):
    mtype: MessageTypes = NotImplemented
    _can_send_carbon: bool = NotImplemented
    STRIP_SHORT_DELAY = False
    USE_STANZA_ID = False

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
        thread = kwargs.pop("thread", None)
        if carbon and self._can_send_carbon:
            # the msg needs to have jabber:client as xmlns, so
            # we don't want to associate with the XML stream
            msg_cls = Message
        else:
            msg_cls = self.xmpp.Message  # type:ignore
        msg = msg_cls(sfrom=mfrom, stype=self.mtype, sto=mto, **kwargs)
        if body:
            msg["body"] = body
            state = "active"
        if thread:
            known_threads = self.session.threads.inverse  # type:ignore
            msg["thread"] = known_threads.get(thread) or str(thread)
        if state:
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
        elif self.USE_STANZA_ID:
            msg["stanza_id"]["id"] = str(uuid4())
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
            if reply_to_author:
                msg["reply"]["to"] = reply_to_author
            if reply_to_fallback_text:
                msg["feature_fallback"].add_quoted_fallback(reply_to_fallback_text)
