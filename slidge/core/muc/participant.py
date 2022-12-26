import string
from copy import copy
from datetime import datetime
from typing import Generic, Optional, Union

from slixmpp import JID, InvalidJID, Message, Presence
from slixmpp.types import MessageTypes

from slidge.core.contact import LegacyContact
from slidge.core.mixins import MessageMixin, PresenceMixin
from slidge.util import SubclassableOnce
from slidge.util.types import LegacyMessageType, LegacyMUCType


class LegacyParticipant(
    Generic[LegacyMUCType], PresenceMixin, MessageMixin, metaclass=SubclassableOnce
):
    mtype: MessageTypes = "groupchat"
    USE_STANZA_ID = True
    STRIP_SHORT_DELAY = False

    def __init__(self, muc: LegacyMUCType, nickname: str, is_user=False):
        self.muc = muc
        self.session = session = muc.session
        self.log = session.log
        self.user = session.user
        self.xmpp = session.xmpp
        self.role = "participant"
        self.affiliation = "member"
        self.is_user = is_user

        self.nickname = nickname
        self.log.debug("NEW PARTICIPANT: %r", nickname)

        j: JID = copy(self.muc.jid)  # type:ignore
        try:
            j.resource = nickname
        except InvalidJID:
            j.resource = (
                "".join(x for x in nickname if x in string.printable)
                + " [renamed by slidge]"
            )
        self.jid = j

        self.log.debug("NEW PARTICIPANT: %r", self)
        self.contact: Optional["LegacyContact"] = None

    def __repr__(self):
        return f"<{self.__class__} {self.nickname} of {self.muc}>"

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        status_codes: Optional[set[int]] = None,
        **presence_kwargs,
    ):
        p = super()._make_presence(last_seen=last_seen, **presence_kwargs)
        p["muc"]["affiliation"] = self.affiliation
        p["muc"]["role"] = self.role
        self.log.debug("Presence - contact: %r", self.contact)
        if self.contact:
            p["muc"]["jid"] = self.contact.jid
        codes = status_codes or set()
        if self.is_user:
            codes.add(110)
        p["muc"]["status_codes"] = codes
        return p

    def _send(
        self,
        stanza: Union[Message, Presence],
        full_jid: Optional[JID] = None,
        **send_kwargs,
    ):
        if full_jid:
            stanza["to"] = full_jid
            stanza.send()
        else:
            for user_full_jid in self.muc.user_full_jids():
                stanza = copy(stanza)
                stanza["to"] = user_full_jid
                stanza.send()

    def send_initial_presence(
        self,
        full_jid: JID,
        status: Optional[str] = None,
        last_seen: Optional[datetime] = None,
        nick_change=False,
        presence_id: Optional[str] = None,
    ):
        """
        Called when the user joins a MUC, as a mechanism
        to indicate to the joining XMPP client the list of "participants".
        (done internally by slidge)

        :param full_jid: Set this to only send to a specific user XMPP resource.
        :param status: a presence message, eg "having a bug, watching the game"
        :param last_seen: when the participant was last online :xep:`0319` (Last User Interaction in Presence)
        :param nick_change: Used when the user joins and the MUC renames them (code 210)
        :param presence_id: set the presence ID. used internally by slidge
        """
        #  MUC status codes: https://xmpp.org/extensions/xep-0045.html#registrar-statuscodes
        p = self._make_presence(
            pstatus=status,
            last_seen=last_seen,
            status_codes={210} if nick_change else set(),
        )
        if presence_id:
            p["id"] = presence_id
        self._send(p, full_jid)

    def send_text(
        self,
        body: str,
        legacy_msg_id: Optional[LegacyMessageType] = None,
        *,
        when: Optional[datetime] = None,
        reply_to_msg_id: Optional[LegacyMessageType] = None,
        reply_to_fallback_text: Optional[str] = None,
        reply_self=False,
        reply_to_author: Optional["LegacyParticipant"] = None,
        **kwargs,
    ):
        """
        The participant sends a message in their corresponding group chat.

        :param body:
        :param legacy_msg_id:
        :param when:
        :param reply_to_msg_id: Quote another message (:xep:`0461`)
        :param reply_to_fallback_text: Fallback text for clients not supporting :xep:`0461`
        :param reply_self: Set to true is this is a self quote
        :param reply_to_author: The participant that was quoted
        """
        if reply_self:
            reply_to_jid = self.jid
        elif reply_to_author:
            reply_to_jid = reply_to_author.jid
        else:
            reply_to_jid = None
        super().send_text(
            body=body,
            legacy_msg_id=legacy_msg_id,
            when=when,
            reply_to_msg_id=reply_to_msg_id,
            reply_to_fallback_text=reply_to_fallback_text,
            reply_to_jid=reply_to_jid,
            hints={"markable"},
            **kwargs,
        )
