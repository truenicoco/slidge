import string
import warnings
from copy import copy
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import JID, InvalidJID, Message, Presence
from slixmpp.plugins.xep_0045.stanza import MUCAdminItem
from slixmpp.types import MessageTypes

from ...util import SubclassableOnce
from ...util.types import LegacyMessageType
from ..contact import LegacyContact
from ..mixins import ChatterDiscoMixin, MessageMixin, PresenceMixin
from .room import MucType

if TYPE_CHECKING:
    from .room import LegacyMUC


class LegacyParticipant(
    PresenceMixin,
    MessageMixin,
    ChatterDiscoMixin,
    metaclass=SubclassableOnce,
):
    mtype: MessageTypes = "groupchat"
    USE_STANZA_ID = True
    STRIP_SHORT_DELAY = False

    def __init__(self, muc: "LegacyMUC", nickname: str, is_user=False):
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

        j: JID = copy(self.muc.jid)
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
        self._sent_presences_to = set[JID]()

    def __repr__(self):
        return f"<{self.__class__} {self.nickname} of {self.muc}>"

    def _make_presence(
        self,
        *,
        last_seen: Optional[datetime] = None,
        status_codes: Optional[set[int]] = None,
        user_full_jid: Optional[JID] = None,
        **presence_kwargs,
    ):
        p = super()._make_presence(last_seen=last_seen, **presence_kwargs)
        p["muc"]["affiliation"] = self.affiliation
        p["muc"]["role"] = self.role
        self.log.debug("Presence - contact: %r", self.contact)
        codes = status_codes or set()
        if self.is_user:
            codes.add(110)
        if self.muc.type == MucType.GROUP:
            if self.is_user and user_full_jid:
                p["muc"]["jid"] = user_full_jid
                codes.add(100)
            elif self.contact:
                p["muc"]["jid"] = self.contact.jid
                if a := self.contact.get_avatar():
                    p["vcard_temp_update"]["photo"] = a.id
            else:
                warnings.warn(
                    f"Public group but no Contact or user_full_jid associated to {self.jid}",
                )

        p["muc"]["status_codes"] = codes
        return p

    @property
    def DISCO_NAME(self):
        return self.nickname

    def _send(
        self,
        stanza: Union[Message, Presence],
        full_jid: Optional[JID] = None,
        archive_only=False,
        **send_kwargs,
    ):
        if full_jid:
            stanza["to"] = full_jid
            if isinstance(stanza, Message) and full_jid not in self._sent_presences_to:
                self.send_initial_presence(full_jid)
            stanza.send()
        else:
            if isinstance(stanza, Message):
                self.muc.archive.add(stanza, archive_only)
            for user_full_jid in self.muc.user_full_jids():
                stanza = copy(stanza)
                stanza["to"] = user_full_jid
                if (
                    isinstance(stanza, Message)
                    and user_full_jid not in self._sent_presences_to
                ):
                    self.send_initial_presence(user_full_jid)
                stanza.send()

    def mucadmin_item(self):
        item = MUCAdminItem()
        item["nick"] = self.nickname
        item["affiliation"] = self.affiliation
        item["role"] = self.role
        if self.muc.type == MucType.GROUP:
            if self.is_user:
                item["jid"] = self.user.bare_jid
            elif self.contact:
                item["jid"] = self.contact.jid.bare
            else:
                warnings.warn(
                    f"Public group but no Contact or user_full_jid associated to {self.jid}",
                )
        return item

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
        codes = set()
        if nick_change:
            codes.add(210)
        p = self._make_presence(
            pstatus=status,
            last_seen=last_seen,
            status_codes=codes,
            user_full_jid=full_jid,
        )
        if presence_id:
            p["id"] = presence_id
        self._send(p, full_jid)
        self._sent_presences_to.add(full_jid)

    def leave(self):
        """
        To be called only by room. To remove a participant, call
        Room.remove_participant(self) instead.
        """
        p = self._make_presence(ptype="unavailable")
        self._send(p)

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
        :param archive_only: Do not send this message to user, but store it in the archive.
            Meant to be used on room instance creation, to populate its message history.
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

    def get_disco_info(self):
        if self.contact is not None:
            return self.contact.get_disco_info()
        return super().get_disco_info()
