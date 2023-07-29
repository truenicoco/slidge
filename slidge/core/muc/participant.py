import logging
import string
import uuid
import warnings
from copy import copy
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Optional, Union

from slixmpp import JID, InvalidJID, Message, Presence
from slixmpp.plugins.xep_0045.stanza import MUCAdminItem
from slixmpp.types import MessageTypes, OptJid

from ...util import SubclassableOnce, strip_illegal_chars
from ...util.types import LegacyMessageType, MucAffiliation, MucRole, MucType
from ..contact import LegacyContact
from ..mixins import ChatterDiscoMixin, MessageMixin, PresenceMixin

if TYPE_CHECKING:
    from .room import LegacyMUC


class LegacyParticipant(
    PresenceMixin,
    MessageMixin,
    ChatterDiscoMixin,
    metaclass=SubclassableOnce,
):
    mtype: MessageTypes = "groupchat"
    _can_send_carbon = False
    USE_STANZA_ID = True
    STRIP_SHORT_DELAY = False

    def __init__(
        self,
        muc: "LegacyMUC",
        nickname: Optional[str] = None,
        is_user=False,
        is_system=False,
    ):
        super().__init__()
        self.muc = muc
        self.session = session = muc.session
        self.user = session.user
        self.xmpp = session.xmpp
        self.role: MucRole = "participant"
        self.affiliation: MucAffiliation = "member"
        self.is_user = is_user
        self.is_system = is_system

        self._nickname = nickname

        log.debug("Instantiation of: %r", self)

        self.__update_jid(nickname)

        self.contact: Optional["LegacyContact"] = None
        # we track if we already sent a presence for this participant.
        # if we didn't, we send it before the first message.
        # this way, event in plugins that don't map "user has joined" events,
        # we send a "join"-presence from the participant before the first message
        self.__presence_sent = False
        self.log = logging.getLogger(f"{self.user.bare_jid}:{self.jid}")

    def __repr__(self):
        return f"<Participant '{self.nickname}'/'{self.jid}' of '{self.muc}'>"

    def __update_jid(self, nickname: Optional[str]):
        j: JID = copy(self.muc.jid)

        if self.is_system:
            self.jid = j
            return

        if nickname:
            nickname = strip_illegal_chars(nickname)
        else:
            warnings.warn(
                "Only the system participant is allowed to not have a nickname"
            )
            nickname = f"unnamed-{uuid.uuid4()}"

        assert isinstance(nickname, str)

        try:
            j.resource = nickname
        except InvalidJID:
            new = (
                "".join(x for x in nickname if x in string.printable)
                + f"-slidge-{hash(nickname)}"
            )
            warnings.warn(f"Could not use {nickname} as a nickname, using {new}")
            j.resource = new

        self.jid = j

    @property
    def nickname(self):
        return self._nickname

    @nickname.setter
    def nickname(self, new_nickname: str):
        old = self._nickname
        if new_nickname == old:
            return

        cache = getattr(self, "_last_presence", None)
        if cache:
            last_seen = cache.last_seen
            kwargs = cache.presence_kwargs
        else:
            last_seen = None
            kwargs = {}

        kwargs["status_codes"] = {303}

        p = self._make_presence(ptype="unavailable", last_seen=last_seen, **kwargs)
        p["muc"]["item"]["nick"] = new_nickname
        self._send(p)

        self.__update_jid(new_nickname)
        self._nickname = new_nickname

        kwargs["status_codes"] = set()
        p = self._make_presence(ptype="available", last_seen=last_seen, **kwargs)
        self._send(p)

        if old:
            self.muc.rename_participant(old, new_nickname)

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
                    f"Private group but no 1:1 JID associated to '{self}'",
                )

        p["muc"]["status_codes"] = codes
        return p

    @property
    def DISCO_NAME(self):
        return self.nickname

    def __send_presence_if_needed(
        self, stanza: Union[Message, Presence], full_jid: JID, archive_only: bool
    ):
        if (
            archive_only
            or self.is_system
            or self.is_user
            or self.__presence_sent
            or stanza["subject"]
        ):
            return
        if isinstance(stanza, Message):
            self.send_initial_presence(full_jid)

    @cached_property
    def __occupant_id(self):
        if self.contact:
            return self.contact.jid
        elif self.is_user:
            return "slidge-user"
        elif self.is_system:
            return "room"
        else:
            return str(uuid.uuid4())

    def _send(
        self,
        stanza: Union[Message, Presence],
        full_jid: Optional[JID] = None,
        archive_only=False,
        **send_kwargs,
    ):
        stanza["occupant-id"]["id"] = self.__occupant_id
        if isinstance(stanza, Presence):
            self.__presence_sent = True
        if full_jid:
            stanza["to"] = full_jid
            self.__send_presence_if_needed(stanza, full_jid, archive_only)
            if self.is_user:
                assert stanza.stream is not None
                stanza.stream.send(stanza, use_filters=False)
            else:
                stanza.send()
        else:
            if isinstance(stanza, Message):
                self.muc.archive.add(stanza, self, archive_only)
            if archive_only:
                return
            for user_full_jid in self.muc.user_full_jids():
                stanza = copy(stanza)
                stanza["to"] = user_full_jid
                self.__send_presence_if_needed(stanza, user_full_jid, archive_only)
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
                    (
                        f"Public group but no contact JID associated to {self.jid} in"
                        f" {self}"
                    ),
                )
        return item

    def send_initial_presence(
        self,
        full_jid: JID,
        nick_change=False,
        presence_id: Optional[str] = None,
    ):
        """
        Called when the user joins a MUC, as a mechanism
        to indicate to the joining XMPP client the list of "participants".

        Can be called this to trigger a "participant has joined the group" event.

        :param full_jid: Set this to only send to a specific user XMPP resource.
        :param nick_change: Used when the user joins and the MUC renames them (code 210)
        :param presence_id: set the presence ID. used internally by slidge
        """
        #  MUC status codes: https://xmpp.org/extensions/xep-0045.html#registrar-statuscodes
        codes = set()
        if nick_change:
            codes.add(210)

        if self.is_user:
            # the "initial presence" of the user has to be vanilla, as it is
            # a crucial part of the MUC join sequence for XMPP clients.
            kwargs = {}
            last_seen = None
        else:
            cache = getattr(self, "_last_presence", None)
            if cache:
                last_seen = cache.last_seen
                kwargs = cache.presence_kwargs
                if kwargs.get("ptype") == "unavailable":
                    return
            else:
                last_seen = None
                kwargs = {}
        p = self._make_presence(
            last_seen=last_seen, status_codes=codes, user_full_jid=full_jid, **kwargs
        )
        if presence_id:
            p["id"] = presence_id
        self._send(p, full_jid)

    def leave(self):
        """
        To be called only by room. To remove a participant, call
        Room.remove_participant(self) instead.
        """
        p = self._make_presence(ptype="unavailable")
        self._send(p)

    def get_disco_info(self, jid: OptJid = None, node: Optional[str] = None):
        if self.contact is not None:
            return self.contact.get_disco_info()
        return super().get_disco_info()

    def moderate(self, legacy_msg_id: LegacyMessageType, reason: Optional[str] = None):
        m = self._make_message()
        m["apply_to"]["id"] = self._legacy_to_xmpp(legacy_msg_id)
        m["apply_to"]["moderated"].enable("retract")
        m["apply_to"]["moderated"]["by"] = self.jid
        if reason:
            m["apply_to"]["moderated"]["reason"] = reason
        self._send(m)

    def set_room_subject(
        self,
        subject: str,
        full_jid: Optional[JID] = None,
        when: Optional[datetime] = None,
        update_muc=True,
    ):
        if when is None:
            when = datetime.now().astimezone()

        if update_muc:
            self.muc._subject = subject  # type: ignore
            self.muc.subject_setter = self
            self.muc.subject_date = when

        msg = self._make_message()
        msg["delay"].set_stamp(when)
        msg["subject"] = subject or str(self.muc.name)
        self._send(msg, full_jid)


log = logging.getLogger(__name__)
