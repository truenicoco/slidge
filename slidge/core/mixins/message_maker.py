import warnings
from copy import copy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Optional, cast
from uuid import uuid4

from slixmpp import Message
from slixmpp.types import MessageTypes

from ...db.models import GatewayUser
from ...slixfix.link_preview.stanza import LinkPreview as LinkPreviewStanza
from ...util.types import (
    ChatState,
    LegacyMessageType,
    LinkPreview,
    MessageReference,
    ProcessingHint,
)
from .. import config
from .base import BaseSender

if TYPE_CHECKING:
    from ...group.participant import LegacyParticipant


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
        reply_to: Optional[MessageReference] = None,
        carbon=False,
        link_previews: Optional[Iterable[LinkPreview]] = None,
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
        msg = msg_cls(
            sfrom=mfrom,
            stype=kwargs.pop("mtype", None) or self.mtype,
            sto=mto,
            **kwargs,
        )
        if body:
            msg["body"] = body
            state = "active"
        if thread:
            msg["thread"] = self.xmpp.store.sent.get_legacy_thread(
                self.user_pk, str(thread)
            ) or str(thread)
        if state:
            msg["chat_state"] = state
        for hint in hints:
            msg.enable(hint)
        self._set_msg_id(msg, legacy_msg_id)
        self._add_delay(msg, when)
        if link_previews:
            self._add_link_previews(msg, link_previews)
        if reply_to:
            self._add_reply_to(msg, reply_to)
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
        return self.xmpp.store.sent.get_xmpp_id(
            self.session.user_pk, str(legacy_id)
        ) or self.session.legacy_to_xmpp_msg_id(legacy_id)

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

    def _add_reply_to(self, msg: Message, reply_to: MessageReference):
        xmpp_id = self._legacy_to_xmpp(reply_to.legacy_id)
        msg["reply"]["id"] = xmpp_id

        muc = getattr(self, "muc", None)

        if entity := reply_to.author:
            if entity == "user" or isinstance(entity, GatewayUser):
                if isinstance(entity, GatewayUser):
                    warnings.warn(
                        "Using a GatewayUser as the author of a "
                        "MessageReference is deprecated. Use the string 'user' "
                        "instead.",
                        DeprecationWarning,
                    )
                if muc:
                    jid = copy(muc.jid)
                    jid.resource = fallback_nick = muc.user_nick
                    msg["reply"]["to"] = jid
                else:
                    msg["reply"]["to"] = self.session.user_jid
                    # TODO: here we should use preferably use the PEP nick of the user
                    # (but it doesn't matter much)
                    fallback_nick = self.session.user_jid.local
            else:
                if muc:
                    if hasattr(entity, "muc"):
                        # TODO: accept a Contact here and use muc.get_participant_by_legacy_id()
                        # a bit of work because right now this is a sync function
                        entity = cast("LegacyParticipant", entity)
                        fallback_nick = entity.nickname
                    else:
                        warnings.warn(
                            "The author of a message reference in a MUC must be a"
                            " Participant instance, not a Contact"
                        )
                        fallback_nick = entity.name
                else:
                    fallback_nick = entity.name
                msg["reply"]["to"] = entity.jid
        else:
            fallback_nick = None

        if fallback := reply_to.body:
            msg["reply"].add_quoted_fallback(fallback, fallback_nick)

    @staticmethod
    def _add_link_previews(msg: Message, link_previews: Iterable[LinkPreview]):
        for preview in link_previews:
            element = LinkPreviewStanza()
            for i, name in enumerate(preview._fields):
                val = preview[i]
                if not val:
                    continue
                element[name] = val
            msg.append(element)
